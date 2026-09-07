#!/usr/bin/env node
/**
 * 一键启动脚本 — 用户只需运行 `npm start`
 * 自动拉起后端 (FastAPI) + 前端 (Vite Dev Server)
 */
const { spawn } = require('child_process');
const http = require('http');
const path = require('path');
const fs = require('fs');
const net = require('net');

const ROOT = __dirname;
const BACKEND_DIR = path.join(ROOT, 'backend');
const FRONTEND_DIR = path.join(ROOT, 'frontend');

const BACKEND_PORT = 8000;
const FRONTEND_PORT = 3000;
const BACKEND_WAIT_TIMEOUT = 60000;

let backendProc = null;
let frontendProc = null;

// ── 检测可用的 Python ──
function findPython() {
  const { execSync } = require('child_process');
  // 跨平台静默探测：找不到/执行失败返回 null（Windows cmd 不识别 2>/dev/null）
  // 注意：不能用 stdio:'ignore' —— 那样 execSync 会返回 null，导致 .toString() 抛错
  function probe(cmd) {
    try {
      return execSync(cmd, { encoding: 'utf8', windowsHide: true }).trim();
    } catch (e) {
      return null; // 命令不存在或执行失败
    }
  }
  // 优先使用项目自带 venv（避免系统 Python 缺包 / 包冲突）
  const venvPythonWin = path.join(BACKEND_DIR, 'venv', 'Scripts', 'python.exe');
  const venvPythonUnix = path.join(BACKEND_DIR, 'venv', 'bin', 'python');
  for (const venvPath of [venvPythonWin, venvPythonUnix]) {
    if (fs.existsSync(venvPath)) {
      const ver = probe(`"${venvPath}" --version`);
      // 关键依赖检查：openai（多 agent 辩论必需）+ loguru（日志）
      if (ver !== null && probe(`"${venvPath}" -c "import openai, loguru"`) !== null) {
        console.log(`  🐍 使用 venv Python: ${venvPath} (${ver})`);
        return venvPath; // 不带引号，交给 spawn 处理
      }
      console.log('  ⚠️  venv Python 存在但缺包（openai/loguru），尝试其他 Python');
    }
  }
  // 回退：系统 Python（Windows 上通常无 python3 命令，最后加 py 启动器兜底）
  const candidates = ['python3.11', 'python3', 'python', 'py'];
  for (const cmd of candidates) {
    const ver = probe(`${cmd} --version`);
    if (ver === null) continue;
    if (probe(`${cmd} -c "import loguru"`) !== null) {
      console.log(`  🐍 使用系统 Python: ${cmd} (${ver})`);
      return cmd;
    }
  }
  console.log('  ❌ 未找到带 loguru 的 Python，请先安装依赖: pip install loguru openai');
  return null;
}

function isPortFree(port) {
  return new Promise((resolve) => {
    const tester = net.createServer()
      .once('error', () => resolve(false))
      .once('listening', () => tester.close(() => resolve(true)))
      .listen(port);
  });
}

function waitForBackend(port, timeout) {
  const start = Date.now();
  return new Promise((resolve, reject) => {
    function check() {
      const req = http.get(
        { hostname: 'localhost', port, path: '/api/system/health', timeout: 2000 },
        (res) => {
          if (res.statusCode === 200) resolve();
          else retry();
        }
      );
      req.on('error', retry);
      req.on('timeout', () => { req.destroy(); retry(); });
    }
    function retry() {
      if (Date.now() - start > timeout) {
        reject(new Error(`后端在 ${timeout / 1000}s 内未启动，请检查后端依赖是否安装`));
      } else {
        setTimeout(check, 1000);
      }
    }
    check();
  });
}

function killAll() {
  console.log('\n🛑 正在停止所有服务...');
  if (frontendProc) { try { frontendProc.kill('SIGTERM'); } catch (e) {} }
  if (backendProc) { try { backendProc.kill('SIGTERM'); } catch (e) {} }
  setTimeout(() => process.exit(0), 500);
}

process.on('SIGINT', killAll);
process.on('SIGTERM', killAll);

async function main() {
  console.log('═══════════════════════════════════════════════════');
  console.log('  商品期货 Trading Agents 系统');
  console.log('═══════════════════════════════════════════════════\n');

  // 0. 检测环境
  const pythonCmd = findPython();
  if (!pythonCmd) {
    console.log('\n  💡 请先完成 Python 环境配置:');
    console.log('     cd backend');
    console.log('     python -m venv venv');
    console.log('     venv\\Scripts\\pip install -r requirements.txt');
    process.exit(1);
  }

  // 检查后端依赖
  const reqFile = path.join(BACKEND_DIR, 'requirements.txt');
  if (!fs.existsSync(path.join(BACKEND_DIR, '.env'))) {
    console.log('  ⚠️  未找到 backend/.env，正在从模板复制...');
    fs.copyFileSync(path.join(BACKEND_DIR, '.env.example'), path.join(BACKEND_DIR, '.env'));
    console.log('  ✅ 已创建 backend/.env（请填入 API Key）');
  }

  // 检查前端依赖
  const feNodeModules = path.join(FRONTEND_DIR, 'node_modules');
  if (!fs.existsSync(feNodeModules)) {
    console.log('  📦 首次运行，正在安装前端依赖...');
    const { execSync } = require('child_process');
    execSync('npm install --include=dev', { cwd: FRONTEND_DIR, stdio: 'inherit', env: { ...process.env, NODE_ENV: 'development' } });
    console.log('  ✅ 前端依赖安装完成');
  }

  const backendFree = await isPortFree(BACKEND_PORT);
  const frontendFree = await isPortFree(FRONTEND_PORT);

  // 1. 启动后端
  if (backendFree) {
    console.log('\n📦 启动后端服务 (端口 ' + BACKEND_PORT + ')...');
    backendProc = spawn(pythonCmd, ['-m', 'uvicorn', 'main:app', '--host', '0.0.0.0', '--port', String(BACKEND_PORT)], {
      cwd: BACKEND_DIR,
      stdio: ['ignore', 'pipe', 'pipe'],
      // PYTHONUTF8=1：Windows 管道下 stdout/stderr 默认是 GBK(strict)，
      // print 含 emoji 的日志(如 update_to_date 里的 🚀✅)会抛 UnicodeEncodeError
      env: { ...process.env, PYTHONUNBUFFERED: '1', PYTHONUTF8: '1' },
    });

    backendProc.stdout.on('data', (data) => {
      const msg = data.toString().trim();
      if (msg) console.log('  [后端] ' + msg);
    });
    backendProc.stderr.on('data', (data) => {
      const msg = data.toString().trim();
      if (msg) console.log('  [后端] ' + msg);
    });
    backendProc.on('exit', (code) => {
      if (code !== 0 && code !== null) {
        console.log(`\n❌ 后端启动失败 (code ${code})`);
        console.log('  请确认后端依赖已安装: cd backend && pip install -r requirements.txt');
        killAll();
      }
    });

    try {
      console.log('⏳ 等待后端就绪...');
      await waitForBackend(BACKEND_PORT, BACKEND_WAIT_TIMEOUT);
      console.log('✅ 后端已就绪');
    } catch (err) {
      console.log('❌ ' + err.message);
      console.log('  请确认后端依赖已安装: cd backend && pip install -r requirements.txt');
      killAll();
      return;
    }
  } else {
    console.log('✅ 后端已在运行');
  }

  // 2. 启动前端
  if (frontendFree) {
    console.log('\n🖥️  启动前端服务 (端口 ' + FRONTEND_PORT + ')...');
    // Windows 上 npm 只提供 npx.cmd 而无 npx.exe，spawn('npx') 会 ENOENT；
    // 因此直接用 node 运行本地 vite 脚本（跨平台可靠，也便于结束进程）
    const viteBin = path.join(FRONTEND_DIR, 'node_modules', 'vite', 'bin', 'vite.js');
    const useLocalVite = fs.existsSync(viteBin);
    const feCmd = useLocalVite ? process.execPath : 'npx';
    const feArgs = useLocalVite
      ? [viteBin, '--host', '0.0.0.0', '--port', String(FRONTEND_PORT)]
      : ['vite', '--host', '0.0.0.0', '--port', String(FRONTEND_PORT)];
    frontendProc = spawn(feCmd, feArgs, {
      cwd: FRONTEND_DIR,
      stdio: ['ignore', 'pipe', 'pipe'],
      env: { ...process.env, NODE_ENV: 'development' },
    });

    frontendProc.stdout.on('data', (data) => {
      const msg = data.toString().trim();
      if (msg) console.log('  [前端] ' + msg);
    });
    frontendProc.stderr.on('data', (data) => {
      const msg = data.toString().trim();
      if (msg) console.log('  [前端] ' + msg);
    });
    frontendProc.on('exit', (code) => {
      if (code !== 0 && code !== null) {
        console.log(`\n❌ 前端退出 (code ${code})`);
      }
      killAll();
    });
  } else {
    console.log('✅ 前端已在运行');
  }

  console.log('\n═══════════════════════════════════════════════════');
  console.log('  🎉 系统已启动！');
  console.log('  📊 前端界面:  http://localhost:' + FRONTEND_PORT);
  console.log('  📖 API 文档:  http://localhost:' + BACKEND_PORT + '/docs');
  console.log('═══════════════════════════════════════════════════');
  console.log('  按 Ctrl+C 停止所有服务\n');
}

main().catch((err) => {
  console.error('启动失败:', err);
  process.exit(1);
});
