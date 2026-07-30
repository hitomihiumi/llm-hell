import { api, ApiError } from "./client";

export interface Project {
  id: string;
  name: string;
  file_count: number;
  total_bytes: number;
  created_at: string;
}

export interface TreeNode {
  name: string;
  path: string;
  type: "file" | "dir";
  file_id: string | null;
  size_bytes: number;
  token_count: number;
  pinned: boolean;
  children: TreeNode[];
}

export interface UploadResult {
  project: Project;
  ingested_count: number;
  skipped_binary: string[];
}

export const projectsApi = {
  list: () => api.get<Project[]>("/api/projects"),
  create: (name: string) => api.post<Project>("/api/projects", { name }),
  get: (id: string) => api.get<Project>(`/api/projects/${id}`),
  remove: (id: string) => api.delete<{ ok: boolean }>(`/api/projects/${id}`),
  tree: (id: string) => api.get<TreeNode>(`/api/projects/${id}/tree`),
  fileContent: (projectId: string, fileId: string) =>
    api.get<{ path: string; content: string }>(`/api/projects/${projectId}/files/${fileId}/content`),
  repoMap: (id: string) => api.get<{ text: string }>(`/api/projects/${id}/repo-map`),
  setPinned: (projectId: string, fileId: string, pinned: boolean) =>
    api.patch<{ ok: boolean }>(`/api/projects/${projectId}/files/${fileId}`, { pinned }),
  uploadSnippet: (projectId: string, path: string, content: string) =>
    api.post<UploadResult>(`/api/projects/${projectId}/snippet`, { path, content }),
};

/**
 * fetch() has no upload-progress event, so the batched file upload (which
 * can be tens of MB for a whole imported project) goes through XHR instead
 * of the shared `api` wrapper, purely to drive a progress bar.
 */
export function uploadProjectFiles(
  projectId: string,
  files: { relativePath: string; file: File }[],
  onProgress: (fraction: number) => void,
): Promise<UploadResult> {
  return new Promise((resolve, reject) => {
    const formData = new FormData();
    for (const { relativePath, file } of files) {
      formData.append("files", file, relativePath);
    }

    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api/projects/${projectId}/upload`);
    xhr.withCredentials = true;

    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(event.loaded / event.total);
    };

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(JSON.parse(xhr.responseText) as UploadResult);
      } else {
        let message = xhr.statusText;
        try {
          message = JSON.parse(xhr.responseText).detail ?? message;
        } catch {
          // no JSON body
        }
        reject(new ApiError(xhr.status, message));
      }
    };
    xhr.onerror = () => reject(new ApiError(0, "Мережева помилка під час завантаження"));

    xhr.send(formData);
  });
}
