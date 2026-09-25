@echo off
title Upload to GitHub - Pawan Flipkart Label Cropper
cd /d "%~dp0"
where git >nul 2>nul
if errorlevel 1 (
  echo Git installed nahi hai. Pehle install karein: https://git-scm.com/download/win
  pause
  exit /b 1
)
echo.
echo  1. github.com par login karke naya repository banayein (New repository)
echo     Name: flipkart-label-cropper   ^|  PRIVATE rakhein  ^|  README add NA karein
echo  2. Repository ka URL copy karke yahan paste karein.
echo.
set /p REPO=GitHub repo URL (https://github.com/USERNAME/flipkart-label-cropper.git): 
if "%REPO%"=="" (echo URL khali hai & pause & exit /b 1)

if not exist ".git" git init -b main
git config user.name >nul 2>nul || git config user.name "Pawan"
git config user.email >nul 2>nul || git config user.email "pawan@users.noreply.github.com"
git add .
git commit -m "Pawan Flipkart Label Cropper Automatically" 2>nul
git remote remove origin 2>nul
git remote add origin %REPO%
echo.
echo Browser mein GitHub login ka window khulega - Sign in karein...
git push -u origin main
if errorlevel 1 (
  echo Upload fail hua. URL aur login check karein.
) else (
  echo.
  echo DONE - code GitHub par upload ho gaya: %REPO%
)
pause
