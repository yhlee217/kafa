@echo off
REM kafa 폴더 감시 자동 실행 (Windows 시작프로그램용)
REM 경로 2곳(INBOX, OUT)만 본인 환경에 맞게 고치세요.
chcp 65001 >nul
set INBOX=C:\kafa\inbox
set OUT=C:\kafa\out

REM (A) Python 설치형:
kafa-watch "%INBOX%" "%OUT%" --interval 10

REM (B) 단일 실행파일(.exe) 사용 시 위 줄 대신 아래 줄을 쓰세요:
REM "C:\kafa\kafa-watch.exe" "%INBOX%" "%OUT%" --interval 10

pause
