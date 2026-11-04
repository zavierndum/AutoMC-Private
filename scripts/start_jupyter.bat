@echo off
wsl -d Ubuntu -- bash -c "~/start_automc_jupyter.sh"
echo.
echo If VS Code is not already connected, use "Existing Jupyter Server..." with the URL printed above.
pause
