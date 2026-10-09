// SPDX-FileCopyrightText: 2026 Patrick Carey <patrickfcarey@gmail.com>
// SPDX-License-Identifier: GPL-3.0

#pragma once

#include <cstdlib>

// setenv/unsetenv are POSIX; the MSVC CRT spells them _putenv_s.
namespace VR::Env
{
	inline void Set(const char* name, const char* value)
	{
#ifdef _WIN32
		_putenv_s(name, value);
#else
		setenv(name, value, 1);
#endif
	}

	inline void Unset(const char* name)
	{
#ifdef _WIN32
		_putenv_s(name, ""); // an empty value removes the variable
#else
		unsetenv(name);
#endif
	}
}
