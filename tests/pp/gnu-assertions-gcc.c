#assert machine(x86)
#if #machine(x86)
yes
#endif
#if #machine
any
#endif
#unassert machine
#if #machine
still
#endif
#if #system(linux)
default_system
#endif
#if #system(windows)
not_this
#endif
