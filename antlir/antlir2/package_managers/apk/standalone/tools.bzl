"""Pinned release binaries, selected for the execution platform."""

_TOOLS = {
    "apko": (
        "1.2.7",
        "5151b4a79e9a7fd495d5f7591cb25293b747bc9e4eb5929643ba80701bfa8153",
        "801c75d114e40b9aff08bd26342f02a5081e590c1445aaff22bcf3f3b5760ffc",
    ),
    "melange": (
        "0.43.2",
        "337c316a2911191eb1db13d744109489d1a8c0c8bb4530ba599dd4050a3b3c03",
        "27e930312f4a2b0d66081111954106a412111866d4a0aa1eb7fc8ca53b634727",
    ),
}

def apk_tools():
    native.http_file(
        name = "regctl",
        urls = select({
            "prelude//cpu:x86_64": ["https://github.com/regclient/regclient/releases/download/v0.11.2/regctl-linux-amd64"],
            "prelude//cpu:arm64": ["https://github.com/regclient/regclient/releases/download/v0.11.2/regctl-linux-arm64"],
        }),
        sha256 = select({
            "prelude//cpu:x86_64": "d79774ee6d3873ba59119b3d4cc9dba850485fbf54a03a17f66fc836bbbe2785",
            "prelude//cpu:arm64": "71b8deb028d174a20c134c76c77e054e8792f32c834beea4b4da36b56b2f3513",
        }),
        executable = True,
        visibility = ["PUBLIC"],
    )
    for tool, (version, amd64_sha, arm64_sha) in _TOOLS.items():
        native.http_archive(
            name = tool + "-archive",
            urls = select({
                "prelude//cpu:x86_64": ["https://github.com/chainguard-dev/{}/releases/download/v{}/{}_{}_linux_amd64.tar.gz".format(tool, version, tool, version)],
                "prelude//cpu:arm64": ["https://github.com/chainguard-dev/{}/releases/download/v{}/{}_{}_linux_arm64.tar.gz".format(tool, version, tool, version)],
            }),
            sha256 = select({
                "prelude//cpu:x86_64": amd64_sha,
                "prelude//cpu:arm64": arm64_sha,
            }),
            strip_prefix = select({
                "prelude//cpu:x86_64": "{}_{}_linux_amd64".format(tool, version),
                "prelude//cpu:arm64": "{}_{}_linux_arm64".format(tool, version),
            }),
        )
        native.genrule(
            name = tool,
            out = tool,
            cmd = "cp $(location :{}-archive)/{} $OUT && chmod +x $OUT".format(tool, tool),
            executable = True,
            visibility = ["PUBLIC"],
        )
