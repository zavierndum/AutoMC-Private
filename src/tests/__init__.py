import os

# The suite never reads the user's keys file, never traces and starts from the shipped default model (agent/keys.py, agent/tracing.py);
# tests that need any of them set their own.
for _name in ("AUTOMC_ENV_FILE", "AUTOMC_TRACING", "AUTOMC_LLM_PROVIDER"):
    os.environ.pop(_name, None)
