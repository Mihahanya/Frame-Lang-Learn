Set shell = CreateObject("WScript.Shell")
script = Replace(WScript.ScriptFullName, " скрыто.vbs", ".bat")
shell.Run """" & script & """", 0, False
