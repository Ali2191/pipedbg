const { contextBridge, ipcRenderer } = require('electron');

// Expose safe APIs to renderer process
contextBridge.exposeInMainWorld('electronAPI', {
  // Get app metadata
  getAppInfo: () => ({
    version: ipcRenderer.invoke('app:version'),
    name: ipcRenderer.invoke('app:name')
  }),
  
  // Clipboard operations
  copyToClipboard: (text) => {
    navigator.clipboard.writeText(text).catch(err => {
      console.error('Failed to copy:', err);
    });
  },
  
  // Platform info
  getPlatform: () => process.platform,
  
  // Theme preference
  getDarkMode: () => true // Always dark in Electron for now
});

// Prevent any access to dangerous APIs
Object.defineProperty(window, 'require', {
  get: () => {
    throw new Error('require is disabled for security');
  }
});

Object.defineProperty(window, 'module', {
  get: () => {
    throw new Error('module is disabled for security');
  }
});

Object.defineProperty(window, 'process', {
  get: () => {
    throw new Error('process is disabled for security');
  }
});
