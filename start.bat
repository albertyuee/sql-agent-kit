@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

:: ─────────────────────────────────────────────
::  sql-agent-kit  一键启动脚本 (Windows)
:: ─────────────────────────────────────────────

cd /d "%~dp0"

echo.
echo ╔══════════════════════════════════════╗
echo ║       sql-agent-kit  启动脚本        ║
echo ╚══════════════════════════════════════╝
echo.

:: ── 1. 检查 Python ──────────────────────────────
echo [INFO]  检查 Python 版本...
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] 未找到 python，请先安装 Python 3.10+
    pause & exit /b 1
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PY_VER=%%v
echo [OK]    Python %PY_VER%

:: ── 2. 检查 Node.js ─────────────────────────────
echo [INFO]  检查 Node.js 版本...
node --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] 未找到 node，请先安装 Node.js 18+
    pause & exit /b 1
)
for /f %%v in ('node --version') do set NODE_VER=%%v
echo [OK]    Node.js %NODE_VER%

:: ── 3. 检查 .env ────────────────────────────────
if not exist ".env" (
    if exist ".env.example" (
        echo [WARN]  .env 不存在，已从 .env.example 复制，请填写配置后重新运行
        copy ".env.example" ".env" >nul
        pause & exit /b 1
    ) else (
        echo [ERROR] .env 文件不存在，请先创建并填写数据库和 LLM 配置
        pause & exit /b 1
    )
)
echo [OK]    .env 已就绪

:: ── 4. Python 虚拟环境 ──────────────────────────
if not exist ".venv\" (
    echo [INFO]  创建 Python 虚拟环境 .venv ...
    python -m venv .venv
    echo [OK]    虚拟环境已创建
)
call .venv\Scripts\activate.bat
echo [OK]    虚拟环境已激活

:: ── 5. 安装 Python 依赖 ─────────────────────────
echo [INFO]  安装 Python 依赖...
python -m pip install --quiet --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple
python -m pip install --progress-bar on -r requirements-backend.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
echo [OK]    Python 依赖已安装

:: ── 6. 安装前端依赖 ─────────────────────────────
echo [INFO]  安装前端依赖...
cd frontend
if not exist "node_modules\" (
    npm install --silent
)
echo [OK]    前端依赖已安装

:: ── 7. 构建前端 ─────────────────────────────────
echo [INFO]  构建前端...
npm run build
if errorlevel 1 (
    echo [ERROR] 前端构建失败
    cd ..
    pause & exit /b 1
)
echo [OK]    前端构建完成
cd ..

:: ── 8. 创建必要目录 ─────────────────────────────
if not exist "logs\" mkdir logs
if not exist "data\" mkdir data
if not exist "config\" mkdir config

:: ── 9. 准备数据库 ───────────────────────────────
:: 只在「.env 里是 sqlite 且库文件还不存在」时建示例库。
:: MySQL / PostgreSQL 一律不碰：init_db.sql 里有 DROP TABLE，
:: 自动对着用户已有的库跑是破坏性的，必须由人显式发起。
echo [INFO]  检查数据库...
python scripts\init_db.py --dialect sqlite --auto
if errorlevel 1 (
    echo [ERROR] 示例数据库初始化失败
    pause & exit /b 1
)

:: ── 10. 启动后端 ────────────────────────────────
if "%PORT%"=="" set PORT=8000

echo.
echo ════════════════════════════════════════
echo   启动成功！访问地址：http://localhost:%PORT%
echo   按 Ctrl+C 停止服务
echo ════════════════════════════════════════
echo.

uvicorn backend.main:app --host 0.0.0.0 --port %PORT% --reload
