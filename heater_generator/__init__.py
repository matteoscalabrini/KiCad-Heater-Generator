try:
    import pcbnew  # noqa: F401
except ImportError:
    pass
else:
    from .action import HeaterGeneratorPlugin

    HeaterGeneratorPlugin().register()
