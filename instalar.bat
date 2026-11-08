@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title Traductor en vivo - instalacion
echo.
echo  ==========================================
echo    Traductor en vivo - instalacion
echo  ==========================================
echo.

rem ---- 1. Python 3.10 a 3.13 -------------------------------------------------
set "PY="
for %%v in (3.12 3.11 3.13 3.10) do (
    if not defined PY (
        py -%%v -c "import sys" >nul 2>&1 && set "PY=py -%%v"
    )
)
if not defined PY (
    python -c "import sys; sys.exit(0 if (3,10)<=sys.version_info[:2]<=(3,13) else 1)" >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo  [X] No encontre Python 3.10 - 3.13.
    echo      Instala Python 3.12 desde https://www.python.org/downloads/
    echo      y marca la casilla "Add python.exe to PATH". Luego vuelve a abrir este archivo.
    echo.
    pause
    exit /b 1
)
echo  [1/5] Python encontrado: %PY%

rem ---- 2. Entorno propio (no toca el resto del PC) -------------------------------
if not exist ".venv\Scripts\python.exe" (
    %PY% -m venv .venv || (echo  [X] No pude crear el entorno .venv & pause & exit /b 1)
)
set "VPY=.venv\Scripts\python.exe"
echo  [2/5] Entorno listo en .venv

rem ---- 3. Librerias -----------------------------------------------------------------
echo  [3/5] Instalando librerias (puede tardar varios minutos)...
set "PIPX="
if exist "ca-bundle.pem" set "PIPX=--cert ca-bundle.pem"
%VPY% -m pip install --upgrade pip -q %PIPX%
%VPY% -m pip install -r requirements.txt -q %PIPX%
if errorlevel 1 (
    echo.
    echo  [X] Fallo la instalacion de librerias.
    echo      Si dice CERTIFICATE_VERIFY_FAILED: tu antivirus intercepta las conexiones.
    echo      Mira "Problemas comunes" en README.md.
    pause
    exit /b 1
)

rem ---- 4. Tarjeta NVIDIA (opcional) ----------------------------------------------
where nvidia-smi >nul 2>&1
if errorlevel 1 (
    echo  [4/5] No hay tarjeta NVIDIA: se usara el procesador ^(mas lento^).
) else (
    echo  [4/5] Tarjeta NVIDIA encontrada: instalando CUDA para Whisper ^(~2 GB^)...
    %VPY% -m pip install -r requirements-nvidia.txt -q %PIPX%
    if errorlevel 1 echo  [!] No se instalo CUDA; funcionara en el procesador.
)

rem ---- 5. Configuracion y acceso directo -------------------------------------------
if not exist "config.json" copy /y "config.example.json" "config.json" >nul
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$w=New-Object -ComObject WScript.Shell; foreach($f in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))){ $s=$w.CreateShortcut(\"$f\Traductor en vivo.lnk\"); $s.TargetPath='%CD%\.venv\Scripts\pythonw.exe'; $s.Arguments='\"%CD%\traductor.py\"'; $s.WorkingDirectory='%CD%'; $s.IconLocation='%CD%\icono.ico,0'; $s.Description='Traductor en vivo para llamadas'; $s.Save() }"
echo  [5/5] Accesos directos creados: escritorio y menu Inicio.
echo.
echo  Listo. Abre "Traductor en vivo" desde el escritorio.
echo  La primera vez descarga el modelo de Whisper (1,5 a 4,5 GB): ten paciencia.
echo  Para ver quien habla en Discord, sigue la seccion "Nombres de Discord" del README.
echo.
pause
