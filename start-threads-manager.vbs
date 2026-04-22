Set objShell = CreateObject("WScript.Shell")
objShell.CurrentDirectory = "C:\Users\fkgyo\OneDrive\デスクトップ\AI×占い自動運用システム開発\apps\threads-manager"
objShell.Run "cmd /c npm run dev", 0, False
