# agent/llm_layer.py - LLM 增强层（降噪二分类 + 低置信补召回）
"""
双模式 LLM 增强：
1. 真模式（有 OPENAI_API_KEY）：批量结构化二分类 → 剔除 false_positive 降误报 + 低置信补召回
2. dry_run/无 Key：读预录制裁决（fixtures/llm_fixtures.py）

关键特性：
- 调用前先 redaction 脱敏敏感信息
- 超时/重试/成本控制
- 异常时不崩，返回原 findings（降级）
- 置信度回写（source 改为 rule+llm 或 llm）
"""
from __future__ import annotations

import json
import os
import time

# 复用现有模型
from agent.models import Finding
from agent.redaction import redact_finding, redact_text

# LLM 分类 Prompt 模板（结构化输出 JSON） - 降噪二分类（精简版减少 400 错误）
DENOISING_PROMPT = """代码评审专家。判定 findings 真假（TP/FP）。

{findings_json}

输出 JSON 数组:
[{{"rule_id":"规则ID","file":"文件路径","line":行号,"verdict":"TP|FP","reason":"理由"}}]

TP=真实问题需修复（安全/严重bug）。FP=误报无需修复（风格/边界情况）。"""

# LLM 补召回 Prompt 模板（结构化输出 JSON） - 补充新 findings（精简版减少 400 错误）
SUPPLEMENTARY_PROMPT = """代码评审专家。分析代码变更，补充遗漏安全问题。

代码：
{diff_context}

现有findings（不要重复）：
{findings_json}

重点检查现有规则可能遗漏的类型：
1. LDAP注入（search_s/search_ext + 用户输入）
2. SSRF（requests.get/httpx.get/urlopen + tainted参数）
3. XSS（render/Markup/innerHTML + tainted）
4. 开放重定向（redirect + tainted参数）
5. SQL/NoSQL注入、路径穿越、不安全反序列化、竞态条件、硬编码凭证、认证绕过。

输出新发现问题 JSON数组（每项含file/line/category/severity/evidence）：
[{{"rule_id":"LLM{{编号}}","file":"文件路径","line":行号,"title":"问题","evidence":"代码片段","category":"security","severity":"critical/high/medium/low","confidence":0.0-1.0,"recommendation":"建议"}}]

无问题返回[]。"""


def _get_llm_config() -> dict:
    """统一获取 LLM 配置（兼容 OPENAI_* 和 TRPC_AGENT_* 环境变量）

    优先级：OPENAI_* > TRPC_AGENT_* > 默认值

    Returns:
        包含 api_key, base_url, model_name 的配置字典

    Raises:
        ValueError: 当没有可用的 API Key 时
    """
    # 优先读取 OPENAI_API_KEY，如果没有则读取 TRPC_AGENT_API_KEY
    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("TRPC_AGENT_API_KEY")
    base_url = os.getenv("OPENAI_BASE_URL") or os.getenv("TRPC_AGENT_BASE_URL")
    model_name = os.getenv("MODEL_NAME") or os.getenv("TRPC_AGENT_MODEL_NAME") or "gpt-4o-mini"

    if not api_key:
        raise ValueError("需要 OPENAI_API_KEY 或 TRPC_AGENT_API_KEY 环境变量")

    return {"api_key": api_key, "base_url": base_url, "model_name": model_name}


def _findings_to_json(findings: list[Finding]) -> str:
    """将 findings 转换为 JSON 格式供 LLM 分析"""
    findings_data = []
    for f in findings:
        findings_data.append({
            "rule_id": f.rule_id,
            "file": f.file,
            "line": f.line,
            "title": f.title,
            "evidence": f.evidence,
            "category": f.category,
            "severity": f.severity.value,
            "confidence": f.confidence,
        })
    return json.dumps(findings_data, ensure_ascii=False, indent=2)


def _call_llm_for_classification(findings: list[Finding]) -> list[dict]:
    """调用 LLM 进行批量二分类（真模式）

    Args:
        findings: 待分类的 findings 列表

    Returns:
        LLM 返回的裁决列表 [{"rule_id", "file", "line", "verdict", "reason"}]

    Raises:
        TimeoutError: LLM 调用超时
        Exception: LLM API 错误
    """
    # 直接使用 openai 库调用（修复：原 generate_content 方法不存在）
    return _call_llm_with_openai_client(findings)


def _call_llm_with_openai_client(findings: list[Finding]) -> list[dict]:
    """使用 openai 库直接调用（备选方案）

    Args:
        findings: 待分类的 findings 列表

    Returns:
        LLM 返回的裁决列表
    """
    try:
        import openai
    except ImportError:
        raise ImportError("需要安装 openai 库: pip install openai")

    # 使用统一的配置读取函数（兼容 OPENAI_* 和 TRPC_AGENT_*）
    config = _get_llm_config()

    # 创建客户端（带超时）
    client = openai.OpenAI(
        api_key=config["api_key"],
        base_url=config["base_url"] if config["base_url"] else None,
        timeout=90.0,  # 增加超时到90秒（避免超时错误）
    )

    # 分批处理，避免单次请求过大（修400错误）
    max_batch_size = 5  # 进一步减小到5个findings每批（减少400错误）
    all_verdicts = []

    for i in range(0, len(findings), max_batch_size):
        batch_findings = findings[i:i + max_batch_size]

        # 准备输入
        redacted_findings = [redact_finding(f) for f in batch_findings]
        findings_json = _findings_to_json(redacted_findings)
        prompt = DENOISING_PROMPT.format(findings_json=findings_json)

        # 重试机制：指数退避（400错误不重试，超时/5xx/Upstream错误重试）
        max_retries = 3
        for attempt in range(max_retries + 1):
            try:
                # 准备 messages 参数（避免 continuation line 缩进问题）
                messages = [
                    {"role": "system", "content": "代码评审专家，输出纯JSON格式。"},
                    {"role": "user", "content": prompt},
                ]

                response = client.chat.completions.create(
                    model=config["model_name"],
                    messages=messages,
                    temperature=0.1,
                    max_tokens=2000)  # 降低到2000减少400错误

                response_text = response.choices[0].message.content
                verdicts = _parse_llm_response(response_text)
                all_verdicts.extend(verdicts)
                break  # 成功，跳出重试循环

            except Exception as e:
                error_msg = str(e).lower()
                # 400错误不重试，直接抛出
                if "400" in error_msg or "bad request" in error_msg:
                    print("[LLM Layer] 400错误（prompt太大），不重试")
                    raise Exception(f"OpenAI API 400错误: {str(e)}") from e
                # 超时/5xx/Upstream错误指数退避重试
                elif attempt < max_retries and ("timeout" in error_msg or "5" in error_msg or "upstream" in error_msg):
                    wait_time = 2**attempt  # 指数退避：1s, 2s, 4s
                    print(f"[LLM Layer] 调用失败，{wait_time}秒后重试 {attempt + 1}/{max_retries}: {str(e)}")
                    time.sleep(wait_time)
                    continue
                else:
                    raise Exception(f"OpenAI API 调用失败: {str(e)}") from e

    return all_verdicts


def _parse_llm_response(response_text: str) -> list[dict]:
    """解析 LLM 返回的 JSON 响应

    Args:
        response_text: LLM 返回的文本

    Returns:
        裁决列表
    """
    try:
        # 尝试直接解析 JSON
        verdicts = json.loads(response_text)
        if not isinstance(verdicts, list):
            raise ValueError("LLM 返回的不是数组")

        return verdicts

    except json.JSONDecodeError:
        # 尝试提取 JSON 代码块
        import re

        # 匹配 ```json...``` 或 ```...```
        json_match = re.search(r'```(?:json)?\s*(\[.*?\])\s*```', response_text, re.DOTALL)
        if json_match:
            try:
                verdicts = json.loads(json_match.group(1))
                if isinstance(verdicts, list):
                    return verdicts
            except json.JSONDecodeError:
                pass

        # 解析失败，返回空列表（降级）
        print(f"[LLM Layer] JSON 解析失败，响应内容：{response_text[:200]}...")
        return []


def _parse_supplementary_findings(response_text: str) -> list[dict]:
    """解析 LLM 补召回返回的 JSON 响应

    Args:
        response_text: LLM 返回的补召回文本

    Returns:
        新 finding 列表
    """
    try:
        # 尝试直接解析 JSON
        findings = json.loads(response_text)
        if not isinstance(findings, list):
            print(f"[LLM Layer] 补召回响应格式错误，非数组：{type(findings)}")
            return []

        return findings

    except json.JSONDecodeError as e:
        # 尝试提取 JSON 代码块
        import re

        # 匹配 ```json...``` 或 ```...```
        json_match = re.search(r'```(?:json)?\s*(\[.*?\])\s*```', response_text, re.DOTALL)
        if json_match:
            try:
                findings = json.loads(json_match.group(1))
                if isinstance(findings, list):
                    return findings
            except json.JSONDecodeError:
                pass

        # 解析失败，添加日志（MIN-2）
        print(f"[LLM Layer] 补召回 JSON 解析失败：{str(e)}，响应内容：{response_text[:200]}...")
        return []


def _prepare_diff_context(files: list) -> str:
    """准备代码变更上下文用于补召回（IMP-1：使用 files 参数）

    Args:
        files: DiffFile 列表

    Returns:
        脱敏后的代码上下文字符串
    """
    if not files:
        return "无代码变更上下文"

    context_parts = []
    for file in files:
        # 收集文件的添加行（先脱敏，再截断）
        added_lines = []
        for line in file.added_lines:
            # 修复：line 是 ChangedLine 对象，不是字符串
            line_content = line.content if hasattr(line, 'content') else str(line)

            # 先脱敏处理（复用 Task 5 的脱敏逻辑）
            redacted_line, _ = redact_text(line_content)

            # 再截断长行
            if len(redacted_line) > 200:
                redacted_line = redacted_line[:200] + "... [截断]"
            added_lines.append(redacted_line)

        if added_lines:
            context_parts.append(f"文件：{file.path}\n添加行：\n" + "\n".join(added_lines))

    return "\n\n".join(context_parts) if context_parts else "无有效的添加行"


def _call_llm_for_supplementary_findings(existing_findings: list[Finding], files: list) -> list[dict]:
    """调用 LLM 进行补召回（真模式）

    Args:
        existing_findings: 现有 findings 列表（避免重复）
        files: DiffFile 列表（代码变更上下文）

    Returns:
        LLM 返回的新 finding 列表

    Raises:
        TimeoutError: LLM 调用超时
        Exception: LLM API 错误
    """
    # 直接使用 openai 库调用（修复：原 generate_content 方法不存在）
    return _call_llm_with_openai_client_supplementary(existing_findings, files)


def _call_llm_with_openai_client_supplementary(existing_findings: list[Finding], files: list) -> list[dict]:
    """使用 openai 库直接调用补召回（备选方案）

    Args:
        existing_findings: 现有 findings 列表
        files: DiffFile 列表

    Returns:
        LLM 返回的新 finding 列表
    """
    try:
        import openai
    except ImportError:
        raise ImportError("需要安装 openai 库: pip install openai")

    # 使用统一的配置读取函数（兼容 OPENAI_* 和 TRPC_AGENT_*）
    config = _get_llm_config()

    # 创建客户端（带超时）
    client = openai.OpenAI(
        api_key=config["api_key"],
        base_url=config["base_url"] if config["base_url"] else None,
        timeout=90.0,  # 增加超时到90秒（避免超时错误）
    )

    # 准备输入（修400：更激进的精简策略）
    redacted_findings = [redact_finding(f) for f in existing_findings[:8]]  # 进一步限制到8个findings
    findings_json = _findings_to_json(redacted_findings)
    # 精简diff context，只保留前300字符（进一步压缩）
    diff_context_full = _prepare_diff_context(files)
    diff_context = diff_context_full[:300] + "..." if len(diff_context_full) > 300 else diff_context_full
    prompt = SUPPLEMENTARY_PROMPT.format(diff_context=diff_context, findings_json=findings_json)

    # 重试机制：指数退避（400错误不重试，超时/5xx/Upstream错误重试）
    max_retries = 3
    for attempt in range(max_retries + 1):
        try:
            # 准备 messages 参数（避免 continuation line 缩进问题）
            messages = [
                {"role": "system", "content": "代码评审专家，输出纯JSON格式。"},
                {"role": "user", "content": prompt},
            ]

            response = client.chat.completions.create(
                model=config["model_name"],
                messages=messages,
                temperature=0.1,
                max_tokens=1500)  # 进一步降低到1500减少400错误

            response_text = response.choices[0].message.content
            new_findings = _parse_supplementary_findings(response_text)
            return new_findings

        except Exception as e:
            error_msg = str(e).lower()
            # 400错误不重试，直接抛出
            if "400" in error_msg or "bad request" in error_msg:
                print("[LLM Layer] 补召回400错误（prompt太大），不重试")
                raise Exception(f"OpenAI 补召回400错误: {str(e)}") from e
            # 超时/5xx/Upstream错误指数退避重试
            elif attempt < max_retries and ("timeout" in error_msg or "5" in error_msg or "upstream" in error_msg):
                wait_time = 2**attempt  # 指数退避：1s, 2s, 4s
                print(f"[LLM Layer] 补召回失败，{wait_time}秒后重试 {attempt + 1}/{max_retries}: {str(e)}")
                time.sleep(wait_time)
                continue
            else:
                raise Exception(f"OpenAI 补召回 API 调用失败: {str(e)}") from e

    return []  # 重试失败返回空列表


def _apply_verdicts(findings: list[Finding], verdicts: list[dict]) -> list[Finding]:
    """应用 LLM 裁决到 findings（剔除 false_positive，更新置信度和 source）

    Args:
        findings: 原 findings 列表
        verdicts: LLM 裁决列表

    Returns:
        过滤并增强后的 findings 列表
    """
    # 构建裁决查找表
    verdict_map = {}
    for v in verdicts:
        key = f"{v['rule_id']}:{v['file']}:{v['line']}"
        verdict_map[key] = v

    # 应用裁决
    enhanced_findings = []
    for f in findings:
        key = f"{f.rule_id}:{f.file}:{f.line}"
        verdict = verdict_map.get(key)

        if verdict:
            # 如果被标记为 false_positive，跳过（剔除误报）
            # 兼容多种格式：FP, false_positive, false positive, False Positive, 误报
            _v = str(verdict.get("verdict", "")).strip().lower().replace(" ", "_")
            if _v in ("false_positive", "fp", "false", "误报"):
                continue

            # 更新 source 和置信度（IMP-2：区分降噪确认和补召回新增）
            if f.source == "rule":
                # rule 源的 findings 经过 LLM 确认后提升置信度和 source
                f.source = "rule+llm"
                f.confidence = min(f.confidence + 0.15, 1.0)
            # 其他源（ast/sandbox 等）保持原 source 不变

        enhanced_findings.append(f)

    return enhanced_findings


def _convert_supplementary_findings(new_findings_data: list[dict]) -> list[Finding]:
    """将补召回的原始数据转换为 Finding 对象

    Args:
        new_findings_data: LLM 返回的新 finding 原始数据

    Returns:
        Finding 对象列表
    """
    new_findings = []
    for data in new_findings_data:
        try:
            # 解析 severity 字符串为 Severity 枚举
            severity_str = data.get("severity", "medium").lower()
            from agent.models import Severity
            severity_map = {
                "critical": Severity.CRITICAL,
                "high": Severity.HIGH,
                "medium": Severity.MEDIUM,
                "low": Severity.LOW,
            }
            severity = severity_map.get(severity_str, Severity.MEDIUM)

            # 创建 Finding 对象
            finding = Finding(
                severity=severity,
                category=data.get("category", "other"),
                file=data.get("file", "unknown"),
                line=int(data.get("line", 0)),
                title=data.get("title", "LLM 补充发现"),
                evidence=data.get("evidence", ""),
                recommendation=data.get("recommendation", ""),
                confidence=float(data.get("confidence", 0.6)),  # 默认适中置信度
                source="llm",  # 补召回标记为 llm 源
                rule_id=data.get("rule_id", "LLM001"))
            new_findings.append(finding)
        except (ValueError, KeyError) as e:
            # 转换失败时跳过该 finding，添加日志
            print(f"[LLM Layer] 补召回 finding 转换失败：{str(e)}，数据：{data}")
            continue

    return new_findings


def enhance(
        findings: list[Finding],
        files: list,  # DiffFile 列表（IMP-1：用于补召回上下文）
        dry_run: bool = False) -> list[Finding]:
    """LLM 增强：降噪二分类 + 低置信补召回

    Args:
        findings: 规则引擎/Ast 召回的 findings
        files: DiffFile 列表（用于补召回代码变更上下文）
        dry_run: 是否为 dry_run 模式（使用预录制裁决）

    Returns:
        增强后的 findings 列表（剔除 false_positive，可能补召回）
    """
    # 边界检查
    if not findings:
        return findings

    # 检查是否可以使用真 LLM
    has_api_key = bool(os.getenv("OPENAI_API_KEY") or os.getenv("TRPC_AGENT_API_KEY"))
    use_real_llm = not dry_run and has_api_key

    # 阶段 1：降噪二分类（剔除 false_positive）
    if not use_real_llm:
        # dry_run 模式或无 API Key：使用预录制裁决
        from fixtures.llm_fixtures import recorded_verdicts

        # 转换 fixtures 格式为裁决列表
        verdict_list = []
        for key, verdict_data in recorded_verdicts.items():
            # 解析 key: "rule_id:file:line"
            parts = key.split(":")
            if len(parts) == 3:
                rule_id, file_path, line_str = parts
                try:
                    line = int(line_str)
                    verdict_list.append({
                        "rule_id": rule_id,
                        "file": file_path,
                        "line": line,
                        "verdict": verdict_data["verdict"],
                        "reason": verdict_data["reason"],
                    })
                except ValueError:
                    continue

        # 应用降噪裁决
        enhanced_findings = _apply_verdicts(findings, verdict_list)

        # 阶段 2：补召回（dry_run 模式使用预录制的 supplementary_findings）
        try:
            from fixtures.llm_fixtures import supplementary_findings
            new_findings = _convert_supplementary_findings(supplementary_findings)
            # 合并降噪后的 findings 和补召回的新 findings
            enhanced_findings.extend(new_findings)
        except ImportError:
            # 如果 supplementary_findings 不存在，跳过补召回
            pass

        return enhanced_findings

    # 真模式：调用 LLM
    try:
        # 阶段 1：降噪二分类（剔除 false_positive）
        verdicts = _call_llm_for_classification(findings)
        enhanced_findings = _apply_verdicts(findings, verdicts)

        # 阶段 2：补召回（让 LLM 补充新 findings）
        try:
            # 调用 LLM 进行补召回（传入 files 参数作为上下文）
            new_findings_data = _call_llm_for_supplementary_findings(enhanced_findings, files)
            # 转换为 Finding 对象
            new_findings = _convert_supplementary_findings(new_findings_data)
            # 合并降噪后的 findings 和补召回的新 findings
            enhanced_findings.extend(new_findings)
        except (TimeoutError, Exception) as e:
            # 补召回失败不影响降噪结果，仅记录日志
            print(f"[LLM Layer] 补召回失败，跳过：{str(e)}")

        return enhanced_findings

    except (TimeoutError, Exception) as e:
        # LLM 调用失败：降级返回原 findings（不崩）
        # 在生产环境中应该记录日志，这里静默降级
        print(f"[LLM Layer] 调用失败，降级返回原 findings: {str(e)}")
        return findings
