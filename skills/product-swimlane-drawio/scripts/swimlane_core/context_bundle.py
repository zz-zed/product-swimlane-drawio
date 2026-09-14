"""Immutable context members with a final no-clobber completion marker."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat

from . import contracts, semantic_context

BUNDLE_NAMES = {"diagram": "diagram.drawio", "context": "context.json"}


def bundle_error(code, message, **evidence):
    raise contracts.DiagramError(message, code=code, evidence=evidence)


def bundle_json(data):
    return (json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def bundle_hash(data):
    return hashlib.sha256(data).hexdigest()


def bundle_absolute(path):
    path = Path(path)
    if ".." in path.parts:
        bundle_error("delivery/path-unsafe", "Path traversal is not supported")
    return Path(os.path.abspath(path))


def bundle_directory(path):
    """Open all ancestors without following links and retain their inode chain."""
    absolute = bundle_absolute(path)
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    chain = [("/", os.fstat(descriptor).st_dev, os.fstat(descriptor).st_ino)]
    try:
        for component in absolute.parts[1:]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
            observed = os.fstat(descriptor)
            chain.append((component, observed.st_dev, observed.st_ino))
        return descriptor, tuple(chain)
    except OSError as exc:
        os.close(descriptor)
        bundle_error("delivery/path-unsafe", "Directory must exist without symlink ancestors", reason=type(exc).__name__)


def bundle_signature(info):
    return (info.st_dev, info.st_ino, info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


@dataclass(frozen=True)
class BundleInput:
    path: Path
    raw: bytes
    signature: tuple
    ancestors: tuple

    @property
    def sha256(self):
        return bundle_hash(self.raw)

    def verify(self):
        current = bundle_read_input(self.path, max_bytes=max(len(self.raw), semantic_context.MAX_BYTES))
        if current.signature != self.signature or current.ancestors != self.ancestors or current.raw != self.raw:
            bundle_error("delivery/input-changed", "Reviewed input changed before bundle completion", path=str(self.path))


def bundle_read_input(path, *, max_bytes=16 * 1024 * 1024):
    absolute = bundle_absolute(path)
    parent, chain = bundle_directory(absolute.parent)
    descriptor = None
    try:
        descriptor = os.open(absolute.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            bundle_error("delivery/path-unsafe", "Bundle inputs must be regular files without hard links", path=str(absolute))
        chunks, size = [], 0
        while True:
            piece = os.read(descriptor, min(65536, max_bytes + 1 - size))
            if not piece:
                break
            chunks.append(piece)
            size += len(piece)
            if size > max_bytes:
                bundle_error("context/resource-limit", "Explicit input exceeds its byte budget", limit=max_bytes)
        after = os.fstat(descriptor)
        observed = os.stat(absolute.name, dir_fd=parent, follow_symlinks=False)
        if bundle_signature(before) != bundle_signature(after) or bundle_signature(after) != bundle_signature(observed):
            bundle_error("delivery/input-changed", "Input changed while being read", path=str(absolute))
        return BundleInput(absolute, b"".join(chunks), bundle_signature(after), chain)
    except OSError as exc:
        bundle_error("delivery/path-unsafe", "Cannot safely read the explicit input", path=str(absolute), reason=type(exc).__name__)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent)


def bundle_check_distinct(inputs):
    identities = [(item.signature[0], item.signature[1]) for item in inputs]
    if len(set(identities)) != len(identities):
        bundle_error("delivery/path-unsafe", "Explicit input paths must not alias each other")


def verify_bundle(manifest_path, graph_input, context_input):
    try:
        manifest = bundle_read_input(manifest_path, max_bytes=semantic_context.MAX_BYTES)
    except contracts.DiagramError as exc:
        if exc.code == "delivery/path-unsafe" and exc.evidence.get("reason") == "FileNotFoundError":
            bundle_error("delivery/bundle-incomplete", "Explicit completion manifest is missing")
        raise
    if manifest.path.name != "completion.json":
        bundle_error("delivery/bundle-invalid", "Completion filename must be completion.json")
    if (graph_input.path.parent != manifest.path.parent or context_input.path.parent != manifest.path.parent
            or graph_input.path.name != BUNDLE_NAMES["diagram"] or context_input.path.name != BUNDLE_NAMES["context"]):
        bundle_error("delivery/bundle-invalid", "Bundle members must use the fixed names in one directory")
    bundle_check_distinct([graph_input, context_input, manifest])
    data = semantic_context.decode_context(manifest.raw, validate=False).data
    if not isinstance(data, dict) or set(data) != {"bundle_version", "members"}:
        bundle_error("delivery/bundle-invalid", "Invalid completion manifest shape")
    if type(data["bundle_version"]) is not int or data["bundle_version"] != 1:
        bundle_error("delivery/bundle-invalid", "Unsupported bundle version")
    expected = [{"role": role, "name": BUNDLE_NAMES[role], "sha256": item.sha256, "bytes": len(item.raw)}
                for role, item in (("diagram", graph_input), ("context", context_input))]
    members = data["members"]
    if (not isinstance(members, list) or len(members) != 2
            or any(not isinstance(member, dict) or type(member.get("bytes")) is not int for member in members)
            or members != expected):
        bundle_error("delivery/bundle-incomplete", "Completion manifest does not match actual immutable member bytes")
    for item in (graph_input, context_input, manifest):
        item.verify()
    return manifest


def bundle_validate_targets(output, context_output, completion):
    targets = [bundle_absolute(item) for item in (output, context_output, completion)]
    if [item.name for item in targets] != ["diagram.drawio", "context.json", "completion.json"] or len({item.parent for item in targets}) != 1:
        bundle_error("delivery/path-unsafe", "Output bundle requires fixed member names in one new directory")
    directory = targets[0].parent
    descriptor, chain = bundle_directory(directory.parent)
    try:
        try:
            os.stat(directory.name, dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            return directory, chain
        bundle_error("delivery/output-exists", "Output bundle directory already exists", path=str(directory))
    finally:
        os.close(descriptor)


def bundle_write_member(descriptor, name, data):
    target = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400, dir_fd=descriptor)
    try:
        offset = 0
        while offset < len(data):
            count = os.write(target, data[offset:])
            if count <= 0:
                raise OSError("Incomplete member write")
            offset += count
        os.fsync(target)
    finally:
        os.close(target)


def bundle_new_directory(output):
    directory = bundle_absolute(output)
    descriptor, chain = bundle_directory(directory.parent)
    try:
        try:
            os.stat(directory.name, dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            return directory, chain
        bundle_error("delivery/output-exists", "Output bundle directory already exists", path=str(directory))
    finally:
        os.close(descriptor)


def bundle_publish_members(output_directory, members, manifest_header, inputs, verify_completion, *, preflight=None):
    """Publish adapter-selected immutable members, then link one completion marker.

    Protocol adapters supply exact member allowlists and validate the completed
    protocol independently. This mechanism does not authenticate their content.
    """
    delivery = {"stage": "preflight", "committed": False, "complete": False,
                "members_written": [], "directory": str(output_directory), "manifest": None,
                "failed_member": None, "residue": []}
    descriptor = parent = None
    subdirectories = {}
    try:
        names = [item["name"] for item in members]
        if (len(set(names)) != len(names) or not names or any(
                not isinstance(name, str) or name.startswith("/") or "\\" in name
                or name in {"completion.json", ".completion.pending"}
                or any(not part or part in {".", ".."} for part in name.split("/"))
                or len(name.split("/")) > 2 for name in names)):
            bundle_error("delivery/path-unsafe", "Immutable member names must be unique confined relative paths")
        directory, ancestors = preflight() if preflight is not None else bundle_new_directory(output_directory)
        bundle_check_distinct(inputs)
        for item in inputs:
            item.verify()
        parent, current_ancestors = bundle_directory(directory.parent)
        if ancestors != current_ancestors:
            bundle_error("delivery/path-unsafe", "Output ancestors changed after preflight")
        delivery["stage"] = "create-directory"
        os.mkdir(directory.name, mode=0o700, dir_fd=parent)
        descriptor = os.open(directory.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        directory_identity = (os.fstat(descriptor).st_dev, os.fstat(descriptor).st_ino)
        for name in sorted({name.split("/")[0] for name in names if "/" in name}):
            os.mkdir(name, mode=0o700, dir_fd=descriptor)
            subdirectories[name] = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
        for member in members:
            name, raw = member["name"], member["raw"]
            delivery["stage"] = "write-" + member.get("role", "member")
            delivery["failed_member"] = name
            destination = subdirectories[name.split("/")[0]] if "/" in name else descriptor
            bundle_write_member(destination, name.split("/")[-1], raw)
            delivery["failed_member"] = None
            entry = {"name": name, "bytes": len(raw), "sha256": bundle_hash(raw)}
            if "role" in member:
                entry["role"] = member["role"]
            delivery["members_written"].append(entry)
        delivery["stage"] = "verify-members"
        written = []
        for member in members:
            actual = bundle_read_input(directory / member["name"], max_bytes=member.get("limit", 16 * 1024 * 1024))
            if actual.raw != member["raw"]:
                bundle_error("delivery/candidate-preservation-failed", "Bundle member bytes changed after validation")
            written.append(actual)
        manifest_raw = bundle_json({**manifest_header, "members": delivery["members_written"]})
        delivery["stage"] = "prepare-manifest"
        delivery["failed_member"] = ".completion.pending"
        bundle_write_member(descriptor, ".completion.pending", manifest_raw)
        delivery["failed_member"] = None
        delivery["stage"] = "input-recheck"
        for item in inputs:
            item.verify()
        for item in written:
            item.verify()
        current, current_chain = bundle_directory(directory.parent)
        os.close(current)
        observed = os.stat(directory.name, dir_fd=parent, follow_symlinks=False)
        if (current_chain != ancestors or (observed.st_dev, observed.st_ino) != directory_identity
                or not stat.S_ISDIR(observed.st_mode)):
            bundle_error("delivery/path-unsafe", "Output directory identity changed before completion")
        pending = bundle_read_input(directory / ".completion.pending", max_bytes=semantic_context.MAX_BYTES)
        if pending.raw != manifest_raw:
            bundle_error("delivery/candidate-preservation-failed", "Prepared completion manifest changed")
        for item in inputs:
            item.verify()
        for child in subdirectories.values():
            os.fsync(child)
        delivery["stage"] = "commit-manifest"
        os.link(".completion.pending", "completion.json", src_dir_fd=descriptor, dst_dir_fd=descriptor, follow_symlinks=False)
        delivery["committed"] = True
        os.unlink(".completion.pending", dir_fd=descriptor)
        os.fsync(descriptor)
        os.fsync(parent)
        delivery["stage"] = "verify-completion"
        actual = verify_completion(directory / "completion.json", written)
        for item in inputs:
            item.verify()
        delivery["manifest"] = {"path": str(actual.path), "sha256": actual.sha256, "bytes": len(actual.raw)}
        delivery["complete"] = True
        delivery["stage"] = "complete"
        return delivery
    except Exception as exc:
        if descriptor is not None:
            try:
                delivery["residue"] = [{"name": name, "bytes": os.stat(name, dir_fd=descriptor, follow_symlinks=False).st_size}
                                       for name in sorted(os.listdir(descriptor))]
                for prefix, child in sorted(subdirectories.items()):
                    delivery["residue"].extend({"name": prefix + "/" + name,
                                                "bytes": os.stat(name, dir_fd=child, follow_symlinks=False).st_size}
                                               for name in sorted(os.listdir(child)))
            except OSError:
                delivery["residue"] = [{"status": "unavailable"}]
        if isinstance(exc, contracts.DiagramError):
            exc.evidence["delivery"] = delivery
            raise
        if isinstance(exc, FileExistsError) and delivery["stage"] == "create-directory":
            bundle_error("delivery/output-exists", "Another writer already created the output directory", delivery=delivery)
        bundle_error("delivery/io-error", "Context bundle delivery did not complete", delivery=delivery, cause=type(exc).__name__)
    finally:
        for child in subdirectories.values():
            os.close(child)
        if descriptor is not None:
            os.close(descriptor)
        if parent is not None:
            os.close(parent)


def deliver_bundle(output, context_output, completion, graph_raw, context_raw, inputs):
    return bundle_publish_members(
        Path(output).parent,
        [{"role": "diagram", "name": "diagram.drawio", "raw": graph_raw},
         {"role": "context", "name": "context.json", "raw": context_raw, "limit": semantic_context.MAX_BYTES}],
        {"bundle_version": 1}, inputs,
        lambda path, written: verify_bundle(path, written[0], written[1]),
        preflight=lambda: bundle_validate_targets(output, context_output, completion),
    )
