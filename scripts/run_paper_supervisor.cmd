@echo off
rem ASTA paper supervisor launcher (used by the ASTA-Paper-Supervisor scheduled task).
rem Dev-only local values (the same ones as infra/docker-compose.yml); no exchange credentials exist.
cd /d E:\Dev\asta
set ASTA_DATABASE_URL=postgresql+pg8000://asta:asta_dev_only@127.0.0.1:5640/asta
set ASTA_SECRET_KEY=dev-only-local-observation-key-not-a-secret
set DISABLE_SQLALCHEMY_CEXT_RUNTIME=1
set PYTHONUTF8=1
".venv\Scripts\python.exe" scripts\paper_supervisor.py >> data\paper\supervisor.out 2>&1
