/**
 * main.js — Electron entry point for the Nikola sidebar.
 *
 * Creates a narrow, always-on-top browser window and loads index.html.
 */

const { app, BrowserWindow, shell } = require('electron');
const path = require('path');

/** @type {BrowserWindow | null} */
let mainWindow = null;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 380,
    height: 800,
    minWidth: 320,
    minHeight: 500,
    title: 'Nikola',
    backgroundColor: '#0a0a14',
    alwaysOnTop: false,
    webPreferences: {
      preload: path.join(__dirname, 'renderer.js'),
      contextIsolation: false,
      nodeIntegration: true,
    },
  });

  mainWindow.loadFile(path.join(__dirname, 'index.html'));

  // Open external links in the default browser rather than Electron
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

app.whenReady().then(() => {
  createWindow();
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
