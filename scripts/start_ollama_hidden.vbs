' start_ollama_hidden.vbs
' Starts Ollama serve without a visible window
' Save to C:\AdaptiveTraining\scripts\start_ollama_hidden.vbs
' Update the Task Scheduler action to run this script instead
 
Set WShell = CreateObject("WScript.Shell")
WShell.Run """C:\Users\Simon\AppData\Local\Programs\Ollama\ollama.exe"" serve", 0, False
Set WShell = Nothing
 