const { app, BrowserWindow, Menu, ipcMain } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const os = require('os');

let mainWindow;
let pythonProcess;
let serverUrl = 'http://127.0.0.1:8765';

// Spawn Python backend server
function startPythonServer() {
  return new Promise((resolve, reject) => {
    // Get Python executable from venv if available
    const venvPath = path.join(__dirname, '..', '.venv', 'bin', 'python');
    const pythonExe = os.platform() === 'win32'
      ? path.join(__dirname, '..', '.venv', 'Scripts', 'python.exe')
      : venvPath;

    pythonProcess = spawn(pythonExe, [
      '-m', 'pipedbg.webui_server'
    ], {
      cwd: path.join(__dirname, '..'),
      stdio: ['ignore', 'pipe', 'pipe']
    });

    // Wait for server startup
    let startupAttempts = 0;
    const checkServer = setInterval(() => {
      startupAttempts++;
      fetch(`${serverUrl}/api/state`)
        .then(() => {
          clearInterval(checkServer);
          resolve();
        })
        .catch(() => {
          if (startupAttempts > 30) {
            clearInterval(checkServer);
            reject(new Error('Server startup timeout'));
          }
        });
    }, 200);

    pythonProcess.stderr.on('data', (data) => {
      console.error(`Python stderr: ${data}`);
    });

    pythonProcess.on('error', (err) => {
      clearInterval(checkServer);
      reject(err);
    });
  });
}

// Create main window
async function createWindow() {
  try {
    await startPythonServer();
  } catch (err) {
    console.error('Failed to start Python server:', err);
    app.quit();
    return;
  }

  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 900,
    minHeight: 600,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      enableRemoteModule: false,
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true
    }
  });

  mainWindow.loadURL(serverUrl);

  // Open DevTools in development
  if (process.env.NODE_ENV === 'development') {
    mainWindow.webContents.openDevTools();
  }

  mainWindow.on('closed', () => {
    mainWindow = null;
  });

  setupMenu();
}

// Setup application menu
function setupMenu() {
  const template = [
    {
      label: 'File',
      submenu: [
        { label: 'Exit', accelerator: 'CmdOrCtrl+Q', click: () => app.quit() }
      ]
    },
    {
      label: 'Edit',
      submenu: [
        { label: 'Undo', accelerator: 'CmdOrCtrl+Z', role: 'undo' },
        { label: 'Redo', accelerator: 'CmdOrCtrl+Y', role: 'redo' },
        { type: 'separator' },
        { label: 'Cut', accelerator: 'CmdOrCtrl+X', role: 'cut' },
        { label: 'Copy', accelerator: 'CmdOrCtrl+C', role: 'copy' },
        { label: 'Paste', accelerator: 'CmdOrCtrl+V', role: 'paste' }
      ]
    },
    {
      label: 'View',
      submenu: [
        { label: 'Reload', accelerator: 'CmdOrCtrl+R', role: 'reload' },
        { label: 'Force Reload', accelerator: 'CmdOrCtrl+Shift+R', role: 'forceReload' },
        { label: 'Toggle DevTools', accelerator: 'CmdOrCtrl+Shift+I', role: 'toggleDevTools' },
        { type: 'separator' },
        { label: 'Actual Size', accelerator: 'CmdOrCtrl+0', role: 'resetZoom' },
        { label: 'Zoom In', accelerator: 'CmdOrCtrl+Plus', role: 'zoomIn' },
        { label: 'Zoom Out', accelerator: 'CmdOrCtrl+Minus', role: 'zoomOut' }
      ]
    },
    {
      label: 'Help',
      submenu: [
        { label: 'About pipedbg', click: showAbout }
      ]
    }
  ];

  const menu = Menu.buildFromTemplate(template);
  Menu.setApplicationMenu(menu);
}

function showAbout() {
  const { dialog } = require('electron');
  dialog.showMessageBox(mainWindow, {
    type: 'info',
    title: 'About pipedbg',
    message: 'pipedbg – CI/CD Pipeline Debugger',
    detail: 'A visual debugger for GitHub Actions, GitLab CI, and CircleCI workflows.\n\nVersion 3.0.0 (Electron)'
  });
}

// IPC handlers
ipcMain.handle('app:version', () => app.getVersion());
ipcMain.handle('app:name', () => app.name);

// App lifecycle
app.on('ready', createWindow);

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('activate', () => {
  if (mainWindow === null) {
    createWindow();
  }
});

app.on('before-quit', () => {
  if (pythonProcess) {
    pythonProcess.kill();
  }
});
