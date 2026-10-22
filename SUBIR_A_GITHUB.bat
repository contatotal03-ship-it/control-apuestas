@echo off
cls
echo ========================================================
echo   SUBIENDO CAMBIOS A GITHUB - CONTROL APUESTAS Y BANKROLL
echo ========================================================
echo.

set /p msg="Descripcion del cambio (Enter para continuar): "
if "%msg%"=="" set msg=Actualizacion de Calculadora y Bot de Apuestas

echo.
echo [1/3] Agregando archivos...
git add .

echo [2/3] Creando version...
git commit -m "%msg%"

echo [3/3] Enviando a GitHub...
git push origin main

echo.
echo ========================================================
echo   PROCESO TERMINADO - ACCESIBLE EN GITHUB PAGES
echo ========================================================
pause
