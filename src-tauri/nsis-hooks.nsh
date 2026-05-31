!macro NSIS_HOOK_PREINSTALL
  nsExec::ExecToStack 'taskkill /IM "local-story-archive-desktop.exe" /T /F'
  Pop $0
  Pop $1
  nsExec::ExecToStack 'taskkill /IM "wattpad-crawler-desktop.exe" /T /F'
  Pop $0
  Pop $1
  nsExec::ExecToStack 'taskkill /IM "local-story-archive-desktop-backend.exe" /T /F'
  Pop $0
  Pop $1
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  nsExec::ExecToStack 'taskkill /IM "local-story-archive-desktop.exe" /T /F'
  Pop $0
  Pop $1
  nsExec::ExecToStack 'taskkill /IM "wattpad-crawler-desktop.exe" /T /F'
  Pop $0
  Pop $1
  nsExec::ExecToStack 'taskkill /IM "local-story-archive-desktop-backend.exe" /T /F'
  Pop $0
  Pop $1
!macroend
