"use strict";
const { contextBridge, ipcRenderer } = require("electron");
contextBridge.exposeInMainWorld("desktop", {
  info: () => ipcRenderer.invoke("connection-info"),
  connect: (origin) => ipcRenderer.invoke("connect-service", origin),
  cancel: () => ipcRenderer.invoke("cancel-connection"),
  onStatus: (callback) => ipcRenderer.on("connection-status", (_event, message) => callback(message))
});
