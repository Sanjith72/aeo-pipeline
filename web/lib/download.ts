// Browser file download from an in-memory blob. Lives in lib/ so light routes
// (e.g. /agents) can use it without dragging a heavy component module into
// their bundle.
export function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}
