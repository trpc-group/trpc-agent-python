# Change Log


## [0.6.2](https://git.woa.com/trpc-python/trpc-python-agent/trpc-agent/-/tags/v0.6.2) (2026-03-26)
### Features And Improvements
- **A2A**:  服务端支持event callback逻辑
- **skill**: knot-agent 支持非流式调用并通过 RunConfig 透传额外的请求参数
- **Docs**: 更新 tool，knowledge，session，memory相关的文档

### Bug Fixes
- **Skill**: 修复skill在容器沙箱环境下运行注入文件过程失败的问题，当容器环境存在相同目录,可能重新构建文件夹,导致命令执行失败,后续相关注入命令不能正常运行
- **Skill**: 修复skill加载本地压缩文件失败的问题
- **Test**: 更新版本测试用例匹配0.6.1版本号

### Thanks to our Contributors
raylchen(陈雷),jasinluo(罗杰鑫),martianliu(刘锦锋)


## [0.6.1](https://git.woa.com/trpc-python/trpc-python-agent/trpc-agent/-/tags/v0.6.1) (2026-03-20)
### Features And Improvements
- **Tool**:  Agent 支持流式工具调用
- **executors**: 支持 PCG123 沙箱环境运行 executor，可以运行代码和 skill 指定的命令
- **AGUI**: 支持获取http请求体， 框架注入http请求体到ctx.run_config里，通过提供get_agui_http_req方便用户获取http请求体，以获取http头
- **A2A**:  与trpc-agent-go框架的A2A协议扩展字段对齐
- **Agent**: knot-agent 支持非流式调用并通过 RunConfig 透传额外的请求参数

### Bug Fixes
- **Agent**: 修复start_from_last_agent无效的问题， 主要解决问题场景：主Agent下有多个子Agent，主Agent只负责路由，路由之后，后续对话都需要从子Agent开始，框架之前通过配置支持此能力，但子Agent配置disallow_transfer_to_parent时，配置无效
- **Agent**: 修复ClaudeAgent调用LLM时默认设置max_token的问题，主要解决问题场景：在调用腾讯云的Deepseek模型时，max_token是非法字段，将会调用失败，使用ClaudeAgent时默认会设置这个字段，导致调用失败

### Thanks to our Contributors
minchangwei(韦明昌),raylchen(陈雷),ricknie(聂文韬),martianliu(刘锦锋)



## [0.6.0](https://git.woa.com/trpc-python/trpc-python-agent/trpc-agent/-/tags/v0.6.0) (2026-03-06)
### Features And Improvements
- **Graph** 支持Graph，方便用户将框架的LLM、各类Agent、MCP、知识库、代码执行器等编排进图中，通过StateGraph构建图，以GraphAgent形式执行图，方便对接各类服务端（A2A/AG-UI/HTTP/用户定制），图节点可以访问框架的内部执行变量，比如各节点返回内容，历史消息列表等
- **Agent**: 支持KnotAgent，方便对接Knot上配置的Agent
- **Agent**: 支持TransferAgent，方便为远程Agent(TrpcRemoteA2aAgent/KnotAgent等)接入框架多Agent的能力，远程Agent结束后，可以将控制权转到其他合适的Agent
- **Agent**: TeamAgent支持配置回调，方便动态更新instruction
- **Agent**: TeamAgent支持为Leader配置Skill，方便扩展Leader的能力
- **Agent**: TeamAgent支持配置num_member_history_runs，用于控制Member可见历史几轮的会话
- **Agent**: TrpcA2aAgentService的Cancel支持解析get_user回调配置的user_id
- **Agent**: 优化TrpcRemoteA2aAgent的Cancel时机，之前客户端触发Cancel之后，需要等服务端回包才会开始走Cancel逻辑，如果服务端不回包，则无法开始服务端的Cancel，现在客户端触发Cancel后立即开始服务端Cancel
- **Memory**: MemoryService支持Mem0
- **Memory**: MemoryService支持定时清理
- **Session**: SessionService支持定时清理
- **Skill**: Skill支持通过Http下载，方便用户托管Skill在远程（需要打包成tar/gzip等形式）
- **Model**: 支持LiteLLMModel，方便对接近1600+种LLM
- **Eval**: 支持系列LLM评估指标：llm_final_response（由LLM裁判agent的实际回答和预期回答）、llm_rubric_response（为LLM裁判指定评估规则以评估agent实际回答的质量）、llm_rubric_knowledge_recall（为LLM裁判指定评估规则以评估agent召回知识的质量）
- **Eval**: 支持组合多个评估指标，评估一个Agent测试集
- **Eval**: 支持配置case_eval_parallelism并发评估，提高LLM系列指标评估的速度
- **Eval**: 支持配置case_parallelism，并发拉起多个Agent的测试集，提高评估的速度
- **Eval**: 支持评估指标计算完成后，汇总其pass@k（k个中通过1个）与pass^k（k个中全通过）
- **Eval**: 支持仅使用指标评估模块，而不拉起Agent运行，方便用户填入任意Agent（非tRPC-Agent框架也可以）执行轨迹及期望行为，评估其Agent的效果
- **Eval**: 支持把评估结果输出成Json文件到指定路径
- **Eval**: 支持用户注册自定义match逻辑
- **Eval**: 支持用户为LLM裁判注册自定义工具，LLM裁判通过调用工具实现更复杂打分场景
- **Eval**: 支持用户注册自定义Runner，推理阶段通过用户传入Runner进行推理，以适应复杂agent系统评估场景
- **Eval**: 支持用户在 *.evalset.json 文件里，通过eval_cases->context_messages注入额外的上下文信息，于补充背景信息、角色设定或样本示例，便于对比不同模型与提示词组合的能力

### Bug Fixes
- **Agent**: 修复Team下，num_history_runs（leader可见过去几轮会话）设置无效的问题
- **Callback**: 修复配置before_xxx_callback，但没有配置after_xxx_callback时，before_xxx_callback没有执行的问题
- **Environment**: 修复Python310下，runner执行asyncio.timeout报错的问题
- **Server**: 修复DebugServer无法单agent模式下，无法识别相对路径，从而导致agent加载失败的问题

### Thanks to our Contributors
minchangwei(韦明昌),raylchen(陈雷),ricknie(聂文韬),martianliu(刘锦锋)

## [0.5.2](https://git.woa.com/trpc-python/trpc-python-agent/trpc-agent/-/tags/v0.5.0) (2026-01-30)
### Features And Improvements
- **Agent**: LangGraphAgent支持使用sub_graph
- **Agent**: LangGraphAgent支持在Node里，流式返回框架的Event
- **Skill**: 支持在 Skill 的 Markdown 描述文件里，通过特定的格式，指定Skil涉及的Agent Tool Name（业务为Agent写的Python函数），框架将会按格式动态加载当前Skill涉及的工具，防止一次加载工具数量过多描述，导致Agent能力降低的问题
- **Service**: AgUi服务支持通过插件形式捕获框架返回的Event，自定义转成相应的AgUi返回消息体
- **Service**: A2A服务支持通过回调协议从A2A请求消息里解析user_id
- **Trace**: 修复Langfuse在配置AgentTool时上报错误Span Name的问题
- **Trace**: Cancel增加相关Trace信息
- **Trace**: agent_run span展示Agent完整的执行操作，方便直接看到Agent的执行过程，而不是一个个点call_llm，tool等span查看执行细节

### Bug Fixes
- **Agent**: 修复Agent在配置MCP时，在Python310执行runner.close因asyncio旧版本不兼容导致失败的问题
- **Eval**: 修复Evaluation在Agent产生空文本时校验失败的问题
- **Tool**: 修复配置多个set_model_response tool时，部分set_model_response tool的方法签名、注释被替换，导致工具运行失败的问题

### Thanks to our Contributors
minchangwei(韦明昌),raylchen(陈雷),ricknie(聂文韬)

## [0.5.1](https://git.woa.com/trpc-python/trpc-python-agent/trpc-agent/-/tags/v0.5.1) (2026-01-19)

### Bug Fixes
- **A2a**: 修复 a2a 协议的元数据字段前缀不匹配导致的兼容问题
- **agui**: 修复agui注册协议名称的时不匹配的问题

### Thanks to our Contributors
raylchen(陈雷)


## [0.5.0](https://git.woa.com/trpc-python/trpc-python-agent/trpc-agent/-/tags/v0.5.0) (2026-01-16)
### Features And Improvements
- **Agent**: Agent支持Cancel的能力，可以中断正在运行的Agent，Agent会保存中断附近的内容及中断操作，方便用户多轮对话中能及时改进Agent的输出
- **Agent**: Agent支持配置模型创建回调，方便Agent能动态切换model（比如解析请求里模型配置再发起调用）
- **Agent**: ClaudeAgent环境初始化时，setup_claude_env支持配置模型创建回调，方便服务启动之后，能动态设置claude-code默认模型的配置
- **Agent**: AgUiAgent支持上传文件、图片等类型的
- **Agent**: Team模式支持返回父Agent
- **Code Executor**: code_executor支持异步执行code
- **Tool**: 支持agent常用的文件读写、搜索工具，包括Read、Write、Edit、Glob、Bash、Grep等
- **Skill**: 支持配置Skill中命令行执行的超时时间
- **Tracing**: TrpcRemoteA2aAgent与ClaudeAgent上报更多的Trace信息（上报call_llm与tool_call的Trace）
- **Examples**: examples下增加系列示例：Teams示例、Filter示例、Agent Cancel示例、trpc_a2a示例、trpc_agui示例

### Bug Fixes
- **Model**: 修复Model Filter执行不能正常结束的问题
- **Agent**: 修复AgUiAgent未能正常运行前端CopliotKit传过来工具的问题（比如更改前端颜色的工具）
- **Skill**: 修复skill目录和环境变量冲突的问题
- **Skill**: 修复读取文件时，未能递归引用文件的问题

### Thanks to our Contributors
minchangwei(韦明昌),raylchen(陈雷),ricknie(聂文韬),suziliu(刘豪)

## [0.5.0a1](https://git.woa.com/trpc-python/trpc-python-agent/trpc-agent/-/tags/v0.5.0a0) (2026-01-09)
### Features And Improvements
- **Agent**: setup_claude_env支持配置模型创建回调，方便服务启动之后，能动态设置claude-code默认模型的配置

### Thanks to our Contributors
minchangwei(韦明昌)

## [0.5.0a0](https://git.woa.com/trpc-python/trpc-python-agent/trpc-agent/-/tags/v0.5.0a0) (2026-01-08)
### Features And Improvements
- **Code Executor**: code_executor支持异步执行code
- **Tool**: 支持agent常用工具：Read、Write、Edit、Glob、Bash、Grep等
- **Agent**: Agent支持配置模型创建回调，方便Agent能动态切换model（比如解析请求里模型配置再发起调用）

### Thanks to our Contributors
minchangwei(韦明昌),raylchen(陈雷)

## [0.4.1](https://git.woa.com/trpc-python/trpc-python-agent/trpc-agent/-/tags/v0.4.1) (2025-12-31)

### Features And Improvements
- **Tracing**: zhiyan-llm提供public的exporter

### Bug Fixes
- **A2A**: 修复 TrpcRemoteA2aAgent 重复收到完整文本的问题
- **Tool**: 修复 FunctionTool 修饰的函数返回 BaseModel 类型失败的问题
- **Storage**: 修复 mysql 数据类型编码的问题
- **Session**: 修复 SqlSessionService 更新出现的主键冲突的问题
- **Session**: 修复 SqlSessionService 追加 event 的时间冲突问题
- **Langgraph**: 修复 LangGraphAgent 序列化出现的类型错误
- **CodeExecution**: 修复 CodeExecution 出现的结果类型错误

### Thanks to our Contributors
minchangwei(韦明昌),raylchen(陈雷)

## [0.4.0](https://git.woa.com/trpc-python/trpc-python-agent/trpc-agent/-/tags/v0.4.0) (2025-12-26)
### Features And Improvements
- **Agent**: 多Agent支持从最后一个Agent开始继续下一轮对话
- **Model**: 支持AntropicModel
- **Knowledge**: 支持 trag 知识库
- **Knowledge**: 代码及文档适配langchain v1及v0.3版本
- **Dependency**: 修改trpc-agent依赖的langchain版本
- **Example**: 补充claude-agent使用skill的example
- **Example**: llm agent 示例代码整理
- **Teams**: 多Agent编排支持Agno的Team模式
- **Teams**: Team模式支持嵌套Team的用法
- **Skill**: 支持 Skill 能力

### Bug Fixes
- **Model**: 修复openai_model在使用add_tools_to_prompt时function id没有使用uuid的问题
- **Agent**: 修复LLmAgent在工具不存在或调用失败时，未正确构造相关信息给LLM的问题
- **Model**: 修复venus上gpt-5.1调用失败的问题

### Thanks to our Contributors
minchangwei(韦明昌),raylchen(陈雷),suziliu(刘豪),martianliu(刘锦锋)


## [0.3.3](https://git.woa.com/trpc-python/trpc-python-agent/trpc-agent/-/tags/v0.3.3) (2025-12-19)
### Features And Improvements
- **Tracing**: 支持上报到Galileo
- **Memory**: Reids Memory支持中文搜索

### Bug Fixes
- **Agent**: 修复LLmAgent在工具不存在或调用失败时，未正确构造相关信息给LLM的问题
- **Model**: 修复venus上gpt-5.1调用失败的问题
- **Model**: 修复模型返回tool的参数为空的导致tool不被调用的问题
- **Tool**: 修复MCPToolset访问不可达下游导致Agent启动不了的问题
- **Tracing**: 修复Agent调用工具抛异常时无trace的问题

### Thanks to our Contributors
minchangwei(韦明昌),raylchen(陈雷),huyanazhang(张厚源),toraxie(谢虎成)

## [0.4.0a0](https://git.woa.com/trpc-python/trpc-python-agent/trpc-agent/-/tags/v0.4.0a0) (2025-12-12)
### Features And Improvements
- **Model**: 支持AntropicModel
- **Knowledge**: 支持 trag 知识库
- **Knowledge**: 代码及文档适配langchain v1及v0.3版本
- **Dependency**: 修改trpc-agent依赖的langchain版本

### Bugfix
- **Agent**: 修复LLmAgent在工具不存在或调用失败时，未正确构造相关信息给LLM的问题
- **Model**: 修复venus上gpt-5.1调用失败的问题

### Thanks to our Contributors
minchangwei(韦明昌),raylchen(陈雷),huiyongchen(陈惠勇),huyanazhang(张厚源)

## [0.3.2](https://git.woa.com/trpc-python/trpc-python-agent/trpc-agent/-/tags/v0.3.2) (2025-12-05)
### Features And Improvements
- **Agent**: ClaudeAgent支持配置temperature等超参数
- **Agent**: LlmAgent支持通过配置关闭或者改写框架内置提示词注入（比如为LlmAgent配置sub_agent时，框架会自动注入如何转发到sub_agent）
- **Tracing**: zhiyan-llm上报带上节点ip信息
- **Tracing**: zhiyan-llm上报支持配置更多sdk的参数（比如max_span_attribute_length，max_span_attributes等）
- **Tracing**: Tracing上报支持展示state变化情况，方便在链路中根据state信息排查问题

### Bug Fixes
- **Agent**: 修复LlmAgent在配置sub_agents时，多轮对话下，第二轮对话从最后一个回答的子Agent开始，而不是从入口Agent开始的问题
- **Summarizer**: 修复LLM响应无token统计导致Summarizer模块设置失败的问题

### Thanks to our Contributors
minchangwei(韦明昌)

## [0.3.1](https://git.woa.com/trpc-python/trpc-python-agent/trpc-agent/-/tags/v0.3.1) (2025-11-20)
### Features And Improvements
- **Event**: 支持visible字段控制是否被Runner返回
- **Langfuse**: 上报去除A2A Tracing
- **AGUI**: 底层实现对接 trpc-fastapi
- **Test**: 添加trpc_agent/models的单测

### Bug Fixes
- **A2A**: 修复TrpcRemoteA2aAgent不支持多轮对话的问题
- **Agent**: 修复LlmAgent配置output_key时带入LLM思考内容的问题
- **AGUI**: 修复Human-In-Loop时输入数字执行失败的问题
- **Tool**: 修复mcp_tool返回类型字段包含list时Agent调用失败的问题

### Thanks to our Contributors
minchangwei(韦明昌),raylchen(陈雷)

## [0.3.0](https://git.woa.com/trpc-python/trpc-python-agent/trpc-agent/-/tags/v0.3.0) (2025-11-14)
### Features And Improvements
- **Evaluation**: 支持Agent评估的能力，可以在定义基于工具调用、Agent输出相似情况的评测集，用pytest运行
- **Evaluation**: Agent评估的能力与Debug Server打通，可以使用前端UI中添加、运行评测集
- **Agent**: AgUIAgent支持Human-In-The-Loop收到用户反馈后执行自定义操作
- **Agent**: AgUiAgent支持自动检测Human-In-Loop的请求
- **Agent**: TrpcRemoteA2aAgent支持用户设置业务信息到A2A协议的Metadata字段中
- **Session**: LlmAgent支持配置max_history_messages，控制Agent可见最多的历史消息数量
- **Session**: LlmAgent支持配置message_timeline_filter_mode，控制Agent，只可见本轮的会话（INVOCATION）、可见历史轮次的会话（ALL，默认）
- **Session**: LlmAgent支持配置message_branch_filter_mode，控制在多Agent下，只见自己的会话（EXACT）、只见多Agent链路下前面Agent及自己的会话（PREFIX），见所有Agent的会话（ALL，默认）
- **Session**: Summarizer提供create_session_summary_by_events，支持在Agent运行过程中压缩历史会话
- **Model**: 优化OpenAIModel逻辑，在调用模型服务时，才检查API_KEY等配置是否配置，而不是创建时候检查，方便远程配置（比如rainbow）拉取之后，再统一设置
- **Tracing**: 支持trpc_zhiyanllm插件，相关配置可配置在trpc_python.yaml
- **Tracing**: 支持trpc_langfuse插件，相关配置可配置在trpc_python.yaml

### Bug Fixes
- **Tool**: 修复CodeExecutor执行后Agent立即结束的问题
- **Tool**: 修复python311下，调用runner.close时，close mcp资源失败的问题
- **Model**: 修复在GPT-5下，传入output_schema调用失败的问题
- **A2A**: 修复使用TrpcA2aAgentService部署服务时，出现no attribute '_fastapi'的问题

### Thanks to our Contributors
minchangwei(韦明昌),raylchen(陈雷),ricknie(聂文韬),jkguo(郭海南)

## [0.2.2](https://git.woa.com/trpc-python/trpc-python-agent/trpc-agent/-/tags/v0.2.2) (2025-10-31)
### Features And Improvements
- **Tracing**: zihyan-llm插件支持gRPC方式上报

### Bug Fixes
- **Tool**: 修复mcp工具入参类型可选多个时调用失败的问题
- **Dependency**: 修复使用trpc-agent[all]安装时无法安装所有生态依赖的问题
- **Model**: 修复GPT5模型调用时设置超参数max_tokens被模型服务拒绝的问题
- **Tracing**: 修复zhiyan-llm插件上报无请求数统计的问题
- **A2A**: 修复Agent配置MCP工具时在A2A下运行失败的问题

### Thanks to our Contributors
minchangwei(韦明昌)

## [0.2.1](https://git.woa.com/trpc-python/trpc-python-agent/trpc-agent/-/tags/v0.2.1) (2025-10-24)
### Features And Improvements
- **Agent**: 支持ClaudeAgent，它基于Claude-Code-CLI工具完成任务拆解、执行、反思等操作，能不断地向完成任务的目标迈进。只需简单配置即可达到不错的效果，适合开发处理代码生成、文件系统交互、复杂任务的Agent。
- **Tool**: 使用MCP Tools时，支持北极星访问下游mcp服务
- **Agent**: LlmAgent支持配置`parallel_tool_calls`，让Tool能并发调用
- **Agent**: LlmAgent支持配置include_previous_history以移除父Agents的消息
- **Tracing**: 更好适配Zhiyan-LLM Tracing

### Bug Fixes
- **Model**: 修复OpenAIModel在asyncio.run里调用失败的问题
- **Tool**: 修复ToolPrompt下工具存在中文响应被Unicode编码的问题
- **Agent**: 修复LangGraphAgent与AG-UI一起使用的问题
- **Agent**: 修复LangGraphAgent因LangGraph返回的原始消息未序列化，导致Agent运行出错的问题

### Thanks to our Contributors
minchangwei(韦明昌),raylchen(陈雷),klausluo(罗程)

## [0.2.0](https://git.woa.com/trpc-python/trpc-python-agent/trpc-agent/-/tags/v0.2.0) (2025-09-26)
### Features And Improvements
- **AG-UI**: Agent服务部署支持AG-UI协议
- **AG-UI**: AG-UI协议接入human-in-the-loop
- **AG-UI**: AG-UI服务支持注册自定义http handle
- **Session**: 支持用户传入会话历史记录而不是使用SessionService
- **Model**: OpenAIModel支持deepseek-v3.1的thinking_enabled参数

### Bug Fixes
- **Tool**: 解决使用agent的回调出现InvocationContext为None的问题
- **Tool**: 修复多轮对话使用ToolPrompt调用taiji模型失败的问题
- **Model**: 修复builtin-planner开启think-config不生效的问题

### Thanks to our Contributors
minchangwei(韦明昌),raylchen(陈雷)

## [0.1.3](https://git.woa.com/trpc-python/trpc-python-agent/trpc-agent/-/tags/v0.1.3) (2025-09-19)
### Features And Improvements
- **Agent**: 支持基于LLM输出文本（xml/json格式）的Function Call能力，在使用不支持Function Call的LLM服务下，Agent也能使用Tool；
- **Environment**: 支持在Windows系统下使用；
- **Tool**: 在定义Tool时，支持传入pydantic参数（之前只支持python的基础类型作为参数），方便用户更好地在定义pydantic类时，增加入参的描述；
- **Knowledge**: LangchainKnowledge增加hunyuan embedding的使用指引；

### Bug Fixes
- **Model**: 修复调用Venus上LLM时，Agent调用Tool无参数时，调用失败的问题；
- **Model**: 使用LLM时，如果是思考模型，发起调用时去掉thinking的内容；

### Thanks to our Contributors
minchangwei(韦明昌),raylchen(陈雷),huiyongchen(陈惠勇)

## [0.1.2](https://git.woa.com/trpc-python/trpc-python-agent/trpc-agent/-/tags/v0.1.2) (2025-09-12)
### Features And Improvements
- **Agent**: 优化ReActPlanner下Agent的行为逻辑，Agent能更好地制定计划及实施；
- **Agent**: LlmAgent支持Human-In-The-Loop的能力；
- **Agent**: LangGraphAgent支持Human-In-The-Loop的能力；
- **Agent**: 优化Agent在A2A流式交互的行为，交互行为保持和LlmAgent一致；
- **Agent**: TrpcRemoteA2aAgent支持无需trpc_python.yaml配置也可以发起对远程服务的A2A调用；
- **Model**: OpenAIModel兼容Venus的Thinking模型；
- **Model**: OpenAIModel支持调用混元多模态模型；
- **Session**: 支持MysqlSessionService，可持久化Agent会话历史数据到Mysql里；
- **Tool**: 支持LangchainTool，方便Agent使用Langchain生态的工具；
- **Tool**: 支持LangchainKnowledgeSearchTool，方便将框架的基于Langchain生态的Knowledge接入Agent；
- **Knowledge**: 封装Langchain Knowledge支持langchain在rag方面的几十种组件生态，比如：Document Loader(Pdf等)、Document Spliter(Markdown等)、Embeeding向量数据库（腾讯云向量数据库、Elasticsearch、PGVector等）、Retriver召回组件（BM25Retriever等）、PromptTemplates等，同时输出如何自定义相关组件实现并接入到Langchain Knowledge；
- **Dependency**: trpc-python及部分插件（redis/mysql）支持在mac上运行，trpc-agent框架调整相应依赖版本、环境以使用这些组件；

### Bug Fixes
- **Agent**: 修复sub_agents下，转移到新Agent时的一些问题，分别是新Agent重复调用transfer_to_agent工具，以及trace报错的不是同一trace context的问题；
- **Agent**: 修复ParallelAgent接入trace报错不是同一trace context的问题；
- **Tool**: 修复MCPTool在异步协程中出现的关闭问题；

### Thanks to our Contributors
minchangwei(韦明昌),raylchen(陈雷),huiyongchen(陈惠勇),ricknie(聂文韬)

## [0.1.2a0](https://git.woa.com/trpc-python/trpc-python-agent/trpc-agent/-/tags/v0.1.2a0) (2025-09-03)
### Bug Fixes
- **Tool**: 修复MCPTool在异步协程中出现的关闭问题

### Thanks to our Contributors
raylchen(陈雷)

## [0.1.1](https://git.woa.com/trpc-python/trpc-python-agent/trpc-agent/-/tags/v0.1.1) (2025-09-01)
### Features And Improvements
- **Telemetry**: 支持Langfuse Tracing，兼容上报到低版本的Langfuse（低于v3.90.0）；
- **Agent**: 支持结构化输出（Output Schema），兼容Agent配置Tools的场景（解决使用Tools时，不能启用LLM结构化输出的问题）；
- **Agent**: 支持结构化输入（Input Schema），一般配合AgentTool使用；
- **Agent**: LangGraphAgent支持在Runner运行时传入业务自定义的State；
- **Dependency**: 版本上报、拦截器与tRPC-Python框架解耦，支持独立使用Agent框架；
- **Tool**: MCPToolset支持传入MCP SDK的ClientSession的各参数；
- **Version**: 使用tRPC-Python时，版本上报接入tRPC-Python的版本上报；
- **Docs**: 优化拦截器使用文档、LlmAgent文档新增结构化输入输出及其示例、新增Langfuse的使用文档；

### Bug Fixes
- **LLM**: 修复腾讯云Deepseek上触发工具调用导致Agent运行抛异常的问题；
- **Tool**: 修复MCPTool访问Nodejs MCP Server时，MCP协议字段解析失败的问题；

### Thanks to our Contributors
minchangwei(韦明昌),raylchen(陈雷),klausluo(罗程)


## [0.1.0](https://git.woa.com/trpc-python/trpc-python-agent/trpc-agent/-/tags/v0.1.0) (2025-08-15)
### Features
- **LlmAgent**: 集成Agent通用的工作流及组件，简单配置Prompt、Agent使用的Tool即可使用；
- **Workflow编排**：默认支持LangGraphAgent，对接LangGraph，用户可以用LangGraph来定制单Agent的复杂工作流；
- **Multi-Agent编排**：
  - 预设工作流编排：Chain模式依次执行Agent、Parallel模式并行执行Agent、Cycle模式Loop执行Agent；
  - Agent自动编排：以树的形式组织Agent的交互方式，Agent能通过配置sub_agents将控制流交给子Agent；
  - CustomAgent自定义编排：用户可以自由组合预设工作流，也可以按自定义逻辑实现Agent的编排；
- **Session 管理**: 支持基于 Redis 的分布式 Session 管理.
- **tRPC 生态深度集成**: 自动启用内网监控和统计等增强功能，与现有 tRPC 代码完全兼容.
- **MCP 工具集成**: 集成官方 mcp 支持，提供模型上下文协议 (Model Context Protocol) 功能.
- **Telemetry 支持**: 集成 Galileo，Zhiyan 监控平台支持，提供完整的可观测性能力.
- **Knowledge组件**：可接入用户知识库，默认提供基于LangChain的RAG组件封装；
- **调试支持**: 提供 DebugServer 功能，支持与 adk web 对接进行 Agent 调试.
- **完整示例**: https://git.woa.com/trpc-python/trpc-python-agent/trpc-agent/tree/master/examples
- **示例文档**: 为每个功能模块提供详细的示例代码和使用说明.参考：https://iwiki.woa.com/p/4015767928

### Thanks to our Contributors
minchangwei(韦明昌),raylchen(陈雷),suziliu(刘豪)
