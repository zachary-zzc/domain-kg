"""Canonical original types; Office normalization never changes original ownership."""

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
ODT = "application/vnd.oasis.opendocument.text"
ODS = "application/vnd.oasis.opendocument.spreadsheet"
ODP = "application/vnd.oasis.opendocument.presentation"
XLS = "application/vnd.ms-excel"
LEGACY = {
    "application/msword": ("doc", "docx", DOCX),
    "application/rtf": ("rtf", "docx", DOCX),
    "text/rtf": ("rtf", "docx", DOCX),
    ODT: ("odt", "docx", DOCX),
    "application/vnd.ms-excel": ("xls", "xlsx", XLSX),
    ODS: ("ods", "xlsx", XLSX),
    "application/vnd.ms-powerpoint": ("ppt", "odp", ODP),
}
OFFICE_ZIP_TYPES = {DOCX, XLSX, PPTX, ODT, ODS, ODP}
OLE_TYPES = {"application/msword", "application/vnd.ms-excel", "application/vnd.ms-powerpoint"}
IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif", "image/tiff", "image/bmp"}
OFFICE_VERSION = "office-native-v2/legacy-normalization-v2"
