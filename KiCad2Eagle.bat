@echo off
rem KiCad2Eagle - start GUI (Python 3 z PyQt5, bez okna konsoli)
cd /d "%~dp0"
set "PYW=%LOCALAPPDATA%\Programs\Python\Python311-32\pythonw.exe"
if exist "%PYW%" goto start
set "PYW=%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe"
if exist "%PYW%" goto start
set "PYW=pythonw"
:start
start "" "%PYW%" "%~dp0KiCad2Eagle.pyw"
