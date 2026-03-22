@echo off
chcp 65001 >nul
set "PYTHON=C:\Users\fkgyo\AppData\Local\Programs\Python\Python312\python.exe"
set "PROJECT=C:\Users\fkgyo\OneDrive\デスクトップ\AI×占い自動運用システム開発"

echo [%date% %time%] ポスター実行開始 >> "%PROJECT%\state\auto-post.log"
"%PYTHON%" "%PROJECT%\agents\poster.py" >> "%PROJECT%\state\auto-post.log" 2>&1
echo [%date% %time%] ポスター実行完了 >> "%PROJECT%\state\auto-post.log"
