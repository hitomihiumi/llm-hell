// Normalizes the two ways a user can hand us a folder - the
// `<input webkitdirectory>` picker (where `File.webkitRelativePath` is
// already set) and dropping a folder onto a drop zone (where we have to
// walk the non-standard but widely supported `webkitGetAsEntry` tree
// ourselves) - into a flat `{relativePath, file}` list.

export interface CollectedFile {
  relativePath: string;
  file: File;
}

export function collectFromFileList(fileList: FileList): CollectedFile[] {
  return Array.from(fileList).map((file) => ({
    relativePath: (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name,
    file,
  }));
}

function readEntries(reader: FileSystemDirectoryReader): Promise<FileSystemEntry[]> {
  return new Promise((resolve, reject) => {
    reader.readEntries(resolve, reject);
  });
}

async function readAllEntries(reader: FileSystemDirectoryReader): Promise<FileSystemEntry[]> {
  const all: FileSystemEntry[] = [];
  // readEntries() returns entries in batches; must keep calling until empty.
  while (true) {
    const batch = await readEntries(reader);
    if (batch.length === 0) break;
    all.push(...batch);
  }
  return all;
}

function entryToFile(entry: FileSystemFileEntry): Promise<File> {
  return new Promise((resolve, reject) => entry.file(resolve, reject));
}

async function walkEntry(entry: FileSystemEntry, prefix: string, out: CollectedFile[]): Promise<void> {
  if (entry.isFile) {
    const file = await entryToFile(entry as FileSystemFileEntry);
    out.push({ relativePath: prefix + entry.name, file });
  } else if (entry.isDirectory) {
    const reader = (entry as FileSystemDirectoryEntry).createReader();
    const children = await readAllEntries(reader);
    await Promise.all(children.map((child) => walkEntry(child, `${prefix}${entry.name}/`, out)));
  }
}

export async function collectFromDataTransfer(dataTransfer: DataTransfer): Promise<CollectedFile[]> {
  const items = Array.from(dataTransfer.items);
  const entries = items
    .map((item) => item.webkitGetAsEntry?.())
    .filter((entry): entry is FileSystemEntry => !!entry);

  if (entries.length === 0) {
    // Fallback for browsers without webkitGetAsEntry: flat file list only.
    return Array.from(dataTransfer.files).map((file) => ({ relativePath: file.name, file }));
  }

  const out: CollectedFile[] = [];
  await Promise.all(entries.map((entry) => walkEntry(entry, "", out)));
  return out;
}
