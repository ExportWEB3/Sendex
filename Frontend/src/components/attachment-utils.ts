import { createElement } from 'react';
import { FileText, Film, Image } from 'lucide-react';

export const formatFileSize = (bytes: number) => {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

export const getFileIcon = (type: string) => {
  if (type.startsWith('image/')) return createElement(Image, { className: 'w-4 h-4 text-blue-500' });
  if (type.startsWith('video/')) return createElement(Film, { className: 'w-4 h-4 text-purple-500' });
  return createElement(FileText, { className: 'w-4 h-4 text-gray-500' });
};

export const isImageType = (type: string) => type.startsWith('image/');