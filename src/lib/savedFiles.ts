import { api } from "./api";
import type { SavedFile } from "../types";

/** A file's bytes as base64, the way the backend takes them. */
export function readBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).replace(/^data:[^,]*,/, ""));
    reader.onerror = () => reject(new Error(`Could not read ${file.name}.`));
    reader.readAsDataURL(file);
  });
}

/** Saves files in Mavo (Knowledge), one by one; the first refusal stops with its reason. */
export async function saveFiles(list: FileList | File[]): Promise<SavedFile[]> {
  const saved: SavedFile[] = [];
  for (const file of Array.from(list)) {
    if (file.size > 15 * 1024 * 1024) throw new Error(`${file.name} is over 15 MB.`);
    saved.push(await api.saveFile(file.name, await readBase64(file)));
  }
  return saved;
}

/** Opens a saved file in a new window (a PDF or image viewer), or downloads it. */
export async function openFile(file: SavedFile): Promise<void> {
  const url = URL.createObjectURL(await api.fileBlob(file.id));
  const link = document.createElement("a");
  link.href = url;
  link.download = file.name;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 30000);
}
