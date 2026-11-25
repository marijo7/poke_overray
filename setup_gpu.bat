@echo off
cd /d "%~dp0"
echo Installing RapidOCR + ONNX Runtime DirectML (GPU)...
.venv\Scripts\python -m pip uninstall -y onnxruntime onnxruntime-gpu
.venv\Scripts\python -m pip install -r requirements-gpu.txt
echo.
echo Done. Set "ocr": {"backend": "rapidocr", "device": "dml"} in config.json, then run start.bat.
pause
