#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Preserve acquisition adaptation and isolated compiled-kernel marker as diffs."""

import difflib
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
changes = []


def change(path, new):
    old = path.read_text()
    relative = str(path.relative_to(ROOT))
    assert old != new
    patch = "".join(
        difflib.unified_diff(
            old.splitlines(True),
            new.splitlines(True),
            fromfile=relative + ".original",
            tofile=relative,
        )
    )
    (ROOT / "logs" / (path.name + ".patch")).write_text(patch)
    changes.append(
        {
            "path": relative,
            "before_sha256": hashlib.sha256(old.encode()).hexdigest(),
            "after_sha256": hashlib.sha256(new.encode()).hexdigest(),
        }
    )
    path.write_text(new)


p = ROOT / "src/ocp-wasm/util/common.cmake"
s = p.read_text()
replacement = """function(checkout_git_tag)
  cmake_parse_arguments(PARSE_ARGV 0 _ "" "SRC;URL;TAG;PATCH;BIN" "")
  if(__URL STREQUAL "https://github.com/pybind/pybind11.git" AND __TAG STREQUAL "v3.1.0")
    set(local_source "@ROOT@/src/pybind11")
  elseif(__URL STREQUAL "https://github.com/Tencent/rapidjson.git" AND __TAG STREQUAL "24b5e7a8b27f42fa16b96fc70aade9106cf7102f")
    set(local_source "@ROOT@/src/rapidjson")
  elseif(__URL STREQUAL "https://github.com/fmtlib/fmt.git" AND __TAG STREQUAL "12.2.0")
    set(local_source "@ROOT@/src/fmt")
  elseif(__URL STREQUAL "https://github.com/Open-Cascade-SAS/OCCT.git" AND __TAG STREQUAL "V8_0_1")
    set(local_source "@ROOT@/src/occt")
  else()
    message(FATAL_ERROR "Unexpected source request ${__URL} ${__TAG}")
  endif()
  if(NOT EXISTS "${__SRC}")
    get_filename_component(local_parent "${__SRC}" DIRECTORY)
    file(MAKE_DIRECTORY "${local_parent}")
    file(CREATE_LINK "${local_source}" "${__SRC}" SYMBOLIC RESULT link_result)
    if(NOT link_result STREQUAL "0")
      message(FATAL_ERROR "Local source link failed: ${link_result}")
    endif()
  endif()
endfunction()""".replace("@ROOT@", str(ROOT))
s, n = re.subn(
    r"function\(checkout_git_tag\).*?endfunction\(\)",
    lambda _: replacement,
    s,
    flags=re.S,
)
assert n == 1
replacement = """function(download_url)
  cmake_parse_arguments(PARSE_ARGV 0 _ "" "NAME;URL;DEST;BIN" "")
  if(NOT __URL STREQUAL "https://github.com/CadQuery/OCP/releases/download/8.0.1.1/OCP_src_stubs_Linux.zip")
    message(FATAL_ERROR "Unexpected archive request: ${__URL}")
  endif()
  if(NOT EXISTS "${__DEST}")
    get_filename_component(local_parent "${__DEST}" DIRECTORY)
    file(MAKE_DIRECTORY "${local_parent}")
    file(CREATE_LINK "@ROOT@/src/ocp-generated" "${__DEST}" SYMBOLIC RESULT link_result)
    if(NOT link_result STREQUAL "0")
      message(FATAL_ERROR "Local generated-source link failed: ${link_result}")
    endif()
  endif()
  set(${__NAME}_SOURCE_DIR "${__DEST}" PARENT_SCOPE)
endfunction()""".replace("@ROOT@", str(ROOT))
s, n = re.subn(
    r"function\(download_url\).*?endfunction\(\)", lambda _: replacement, s, flags=re.S
)
assert n == 1
change(p, s)
p = ROOT / "src/occt/src/FoundationClasses/TKernel/OSD/OSD_Process.cxx"
s = p.read_text()
old = "// Emscripten SDK raises TODO exception in runtime while calling getpwuid()\n  return TCollection_AsciiString();"
new = '// Scansor disposable #226 modification, 2026-10-14: compiled-library replacement proof.\n  return TCollection_AsciiString("scansor-226-relinked-occt");'
assert s.count(old) == 1
change(p, s.replace(old, new))
(ROOT / "adaptations.json").write_text(json.dumps(changes, indent=2) + "\n")
print("Preserved local-only source acquisition and compiled OCCT marker adaptations")
