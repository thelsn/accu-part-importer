from __future__ import annotations

from pathlib import Path

STEP_TRANSLATOR_ID = "{90AF7F40-0C01-11D5-8E83-0010B541CD80}"
IGES_TRANSLATOR_ID = "{90AF7F42-0C01-11D5-8E83-0010B541CD80}"


def _dispatch_inventor(visible: bool):
    import pythoncom
    import win32com.client

    try:
        pythoncom.CoInitialize()
    except Exception:
        pass
    inventor = None
    started = False
    try:
        inventor = win32com.client.GetActiveObject("Inventor.Application")
    except Exception:
        inventor = win32com.client.Dispatch("Inventor.Application")
        started = True
    inventor.Visible = bool(visible or started)
    return inventor, started


def _set_prop(prop_set, name: str, value: str) -> None:
    if value is None:
        return
    text = str(value)
    try:
        prop_set.Item(name).Value = text
    except Exception:
        try:
            prop_set.Add(text, name)
        except Exception:
            pass


def _apply_iproperties(doc, part_number: str, description: str, extra: dict[str, str]) -> None:
    try:
        tracking = doc.PropertySets.Item("Design Tracking Properties")
        _set_prop(tracking, "Part Number", part_number)
        _set_prop(tracking, "Description", description)
        _set_prop(tracking, "Stock Number", extra.get("stock_number", ""))
        _set_prop(tracking, "Project", extra.get("project", "Tiny Air"))
        _set_prop(tracking, "Vendor", extra.get("vendor", "Accu"))
    except Exception:
        pass
    try:
        summary = doc.PropertySets.Item("Inventor Summary Information")
        _set_prop(summary, "Title", part_number)
        _set_prop(summary, "Subject", description)
        _set_prop(summary, "Author", "Accu Importer")
        _set_prop(summary, "Comments", extra.get("comments", ""))
    except Exception:
        pass
    try:
        user = doc.PropertySets.Item("Inventor User Defined Properties")
        for key, value in extra.items():
            _set_prop(user, key, value)
    except Exception:
        pass


def _open_with_translator(inventor, source: Path):
    suffix = source.suffix.lower()
    addin_id = IGES_TRANSLATOR_ID if suffix in {".igs", ".iges"} else STEP_TRANSLATOR_ID
    addin = inventor.ApplicationAddIns.ItemById(addin_id)
    if addin is not None and not addin.Activated:
        addin.Activate()
    if addin is None or addin.Automation is None:
        return inventor.Documents.Open(str(source))

    trans = addin.Automation
    context = inventor.TransientObjects.CreateTranslationContext()
    options = inventor.TransientObjects.CreateNameValueMap()
    data = inventor.TransientObjects.CreateDataMedium()
    try:
        context.Type = 524290  # kFileBrowseIOMechanism
    except Exception:
        pass
    data.FileName = str(source)
    try:
        if trans.HasOpenOptions(data, context, options):
            try:
                options.Value["SaveComponentsDuringLoad"] = False
            except Exception:
                pass
    except Exception:
        pass
    trans.Open(data, context, options)
    return inventor.ActiveDocument


def import_cad_to_ipt(
    source: Path,
    ipt_path: Path,
    part_number: str,
    description: str,
    extra: dict[str, str] | None = None,
    visible: bool = True,
    close_after_save: bool = True,
) -> Path:
    extra = extra or {}
    inventor, _started = _dispatch_inventor(visible)
    previous_silent = bool(inventor.SilentOperation)
    inventor.SilentOperation = True
    doc = None
    try:
        try:
            doc = _open_with_translator(inventor, source)
        except Exception:
            doc = inventor.Documents.Open(str(source))
        if doc is None:
            raise RuntimeError("Inventor opened the CAD file but no document is active.")
        _apply_iproperties(doc, part_number, description, extra)
        ipt_path.parent.mkdir(parents=True, exist_ok=True)
        if ipt_path.exists():
            ipt_path.unlink()
        doc.SaveAs(str(ipt_path), False)
        if close_after_save:
            try:
                doc.Close(True)
            except Exception:
                pass
        return ipt_path
    finally:
        inventor.SilentOperation = previous_silent
