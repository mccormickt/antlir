"""Custom execution platform that supports extra constraints like may_run_local."""

def _execution_platform_impl(ctx):
    constraints = {}
    constraints.update(ctx.attrs.cpu_configuration[ConfigurationInfo].constraints)
    constraints.update(ctx.attrs.os_configuration[ConfigurationInfo].constraints)
    for extra in ctx.attrs.extra_constraints:
        constraints.update(extra[ConfigurationInfo].constraints)

    cfg = ConfigurationInfo(constraints = constraints, values = {})

    exec_platform = ExecutionPlatformInfo(
        label = ctx.label.raw_target(),
        configuration = cfg,
        executor_config = CommandExecutorConfig(
            local_enabled = True,
            remote_enabled = False,
            use_windows_path_separators = ctx.attrs.use_windows_path_separators,
        ),
    )

    return [
        DefaultInfo(),
        exec_platform,
        PlatformInfo(label = str(ctx.label.raw_target()), configuration = cfg),
        ExecutionPlatformRegistrationInfo(platforms = [exec_platform]),
    ]

execution_platform = rule(
    impl = _execution_platform_impl,
    attrs = {
        "cpu_configuration": attrs.dep(providers = [ConfigurationInfo]),
        "os_configuration": attrs.dep(providers = [ConfigurationInfo]),
        "extra_constraints": attrs.list(attrs.dep(providers = [ConfigurationInfo]), default = []),
        "use_windows_path_separators": attrs.bool(default = False),
    },
)
