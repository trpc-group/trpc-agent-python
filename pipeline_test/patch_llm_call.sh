_PS_DIR="${TMPDIR:-/tmp}/trpc_agent_patch_llm_call.$$"
mkdir -p "$_PS_DIR"

cat > "$_PS_DIR/sitecustomize.py" <<'PYEOF'
import sys

_TAG = "[patch_llm_call]"


def _log(msg):
    print(f"{_TAG} {msg}", file=sys.stdout, flush=True)


try:
    from trpc_agent_sdk.models._openai_model import OpenAIModel
except Exception as e:
    _log(f"patch SKIPPED: failed to import OpenAIModel ({type(e).__name__}: {e})")
else:
    _orig_init = OpenAIModel.__init__

    def _patched_init(self, *args, **kwargs):
        model_name = args[0] if args else kwargs.get("model_name", "<unknown>")
        before = kwargs.get("enable_thinking", "<unset>")
        kwargs.setdefault("enable_thinking", False)
        _orig_init(self, *args, **kwargs)
        _log(
            f"OpenAIModel(model_name={model_name!r}) init patched "
            f"(caller enable_thinking={before!r}; "
            f"final kwarg={kwargs.get('enable_thinking')!r}; "
            f"self.enable_thinking={getattr(self, 'enable_thinking', '<not-stored>')!r})"
        )

    OpenAIModel.__init__ = _patched_init

    _orig_extract = OpenAIModel._extract_http_options

    def _patched_extract(self, config):
        http_opts = _orig_extract(self, config) or {}
        extra_headers = http_opts.setdefault("extra_headers", {})
        extra_headers.setdefault("X-SMG-Routing-Key", "minchangwei")
        extra_headers.setdefault("X-SMG-Agent-Name", "trpc-python-agent-pipeline")
        extra_body = http_opts.setdefault("extra_body", {})
        chat_template_kwargs = extra_body.setdefault("chat_template_kwargs", {})
        chat_template_kwargs.setdefault("enable_thinking", False)
        return http_opts

    OpenAIModel._extract_http_options = _patched_extract

    _log(
        f"patch INSTALLED on {OpenAIModel.__module__}.{OpenAIModel.__qualname__} "
        f"(layers: __init__ + _extract_http_options; "
        f"forces extra_body.chat_template_kwargs.enable_thinking=False; "
        f"injects X-SMG-Routing-Key and X-SMG-Agent-Name headers)"
    )
PYEOF

export PYTHONPATH="$_PS_DIR${PYTHONPATH:+:$PYTHONPATH}"
unset _PS_DIR
echo "[patch_llm_call] OpenAIModel thinking mode will be disabled and SMG headers will be injected (PYTHONPATH updated)."