"""Standard-library-only limits shared by archive codecs and runtime packaging."""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ProjectArchiveLimits:
    max_archive_bytes: int = 48 * 1024**2
    max_directory_bytes: int = 65536
    max_entries: int = 16
    max_actions_bytes: int = 32 * 1024**2
    max_metadata_bytes: int = 1024**2
    max_member_bytes: int = 32 * 1024**2
    max_expanded_bytes: int = 48 * 1024**2
    chunk_bytes: int = 65536

    def __post_init__(self) -> None:
        if any(type(value) is not int or value < 1 for value in self.__dict__.values()):
            raise ValueError("Project archive limits must be positive integers.")
        if self.max_archive_bytes >= 2**31 or self.max_member_bytes >= 2**31:
            raise ValueError(
                "The initial project archive profile excludes ZIP64 sizes."
            )


def project_archive_profile(
    limits: ProjectArchiveLimits | None = None,
) -> dict[str, object]:
    return {
        "supported": True,
        "version": 1,
        "limits": asdict(limits if limits is not None else ProjectArchiveLimits()),
    }
