from agent import tracing as _tracing

_tracing.force_off()            # before any LangChain call: nothing is traced unless the agent asks for one call (agent/tracing.py)
