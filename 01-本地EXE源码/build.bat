@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PROJECT=%~dp0.."
set "DIST=%~1"
if "%DIST%"=="" set "DIST=%PROJECT%\04-构建与成品\dist"
python -m PyInstaller --noconfirm --clean --distpath "%DIST%" --workpath "%PROJECT%\04-构建与成品\build\pyinstaller" "%~dp0guxi.spec"
if errorlevel 1 exit /b 1
if exist "%DIST%\frontend\css\buttons.css" del /Q "%DIST%\frontend\css\buttons.css"
if exist "%DIST%\frontend\js\ripple.js" del /Q "%DIST%\frontend\js\ripple.js"
xcopy "%~dp0frontend" "%DIST%\frontend" /E /I /Y /Q >nul
if errorlevel 2 exit /b 1
xcopy "%~dp0trading\config" "%DIST%\trading\config" /E /I /Y /Q >nul
if errorlevel 2 exit /b 1
copy /Y "%~dp0config.yaml" "%DIST%\config.yaml" >nul || exit /b 1
copy /Y "%PROJECT%\README.md" "%DIST%\README.md" >nul || exit /b 1
copy /Y "%PROJECT%\05-项目文档\THIRD_PARTY_NOTICES.md" "%DIST%\THIRD_PARTY_NOTICES.md" >nul || exit /b 1
copy /Y "%PROJECT%\DEPLOY_LOCAL.md" "%DIST%\DEPLOY.md" >nul || exit /b 1
copy /Y "%~dp0.env.example" "%DIST%\.env.example" >nul || exit /b 1
if not exist "%DIST%\cache" mkdir "%DIST%\cache"
if not exist "%DIST%\logs" mkdir "%DIST%\logs"
if not exist "%DIST%\data\reports" mkdir "%DIST%\data\reports"
if not exist "%DIST%\data\user-state" mkdir "%DIST%\data\user-state"
echo.
echo Build complete: %DIST%
endlocal
