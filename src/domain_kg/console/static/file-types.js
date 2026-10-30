// Use canonical upload types even when the OS returns no MIME or a legacy alias.
export const FILE_TYPES = Object.freeze({
  txt: "text/plain", md: "text/markdown", markdown: "text/markdown",
  html: "text/html", htm: "text/html", csv: "text/csv", tsv: "text/tab-separated-values", json: "application/json",
  pdf: "application/pdf", doc: "application/msword", rtf: "application/rtf",
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  xls: "application/vnd.ms-excel", xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  ppt: "application/vnd.ms-powerpoint", pptx: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  odt: "application/vnd.oasis.opendocument.text", ods: "application/vnd.oasis.opendocument.spreadsheet",
  odp: "application/vnd.oasis.opendocument.presentation",
  png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", webp: "image/webp",
  gif: "image/gif", tif: "image/tiff", tiff: "image/tiff", bmp: "image/bmp",
});
export function fileMediaType(file) { return FILE_TYPES[file.name.split(".").pop().toLowerCase()] || null; }
