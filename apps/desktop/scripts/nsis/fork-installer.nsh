!macro customHeader
  ; nsis@1.2.1 ships the ANSI nsisunz.dll in both plugin directories.
  !addplugindir /x86-unicode "${PROJECT_DIR}\scripts\nsis"
!macroend

!macro customInstall
  File "/oname=$INSTDIR\resources\LICENSE.nsisunz.txt" "${PROJECT_DIR}\scripts\nsis\LICENSE.txt"
!macroend
