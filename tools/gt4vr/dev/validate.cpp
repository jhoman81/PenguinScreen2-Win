// Standalone harness: run PenguinScreen2's real profile loader + validator on
// a resources dir (shipped profiles) and a user dir (our profile).
#include "Config.h"
#include "VR/VRProfileDB.h"
#include "common/Console.h"

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <string>

namespace EmuFolders
{
	std::string AppRoot, DataRoot, Settings, Bios, Snapshots, Savestates, MemoryCards, Logs, Cheats, Patches,
		Resources, UserResources, Cache, Covers, GameSettings, Textures, InputProfiles, Videos, DebuggerLayouts,
		DebuggerSettings, VRProfiles;
}

int main(int argc, char** argv)
{
	if (argc < 5)
	{
		std::fprintf(stderr, "usage: validate <resources_dir> <user_profiles_dir> <serial> <crc_hex>\n");
		return 2;
	}
	Log::SetConsoleOutputLevel(LOGLEVEL_INFO);
	EmuFolders::Resources = argv[1];
	EmuFolders::VRProfiles = argv[2];
	const std::string serial = argv[3];
	const u32 crc = static_cast<u32>(std::strtoul(argv[4], nullptr, 16));

	const auto& issues = VR::ProfileDB::ValidateAtLaunch();
	std::printf("== launch validation: %zu issue(s)\n", issues.size());
	for (const auto& i : issues)
		std::printf("  ISSUE %s: %s\n", i.file.c_str(), i.message.c_str());
	for (const auto& r : VR::ProfileDB::StereoRailFindings())
		std::printf("  RAIL  %s %s: %.1f arcmin%s - %s\n", r.serial.c_str(), r.site.c_str(), r.arcmin,
			r.from_map ? " (map)" : "", r.message.c_str());
	for (const auto& a : VR::ProfileDB::ProfileAdvisories())
		std::printf("  ADVISE %s %s: %s\n", a.serial.c_str(), a.site.c_str(), a.message.c_str());

	std::printf("== profiles on record\n");
	for (const auto& s : VR::ProfileDB::ListProfiles())
		std::printf("  %s  %s  stereo=%d camera=%d sep=%g conv=%g\n", s.serial.c_str(), s.name.c_str(),
			s.has_stereo, s.has_camera, s.separation, s.convergence);

	const VR::ProfileDB::Profile* p = VR::ProfileDB::Lookup(serial, crc);
	if (!p)
	{
		std::printf("== Lookup(%s, %08X): NOT FOUND\n", serial.c_str(), crc);
		return 1;
	}
	std::printf("== Lookup(%s, %08X): '%s' tier=%d\n", serial.c_str(), crc, p->name.c_str(), static_cast<int>(p->tier));
	if (p->stereo)
	{
		const auto& st = *p->stereo;
		std::printf("  stereo sep=%g conv=%g uv=%d pinQ=%d zDriven=%d map=%u scenes=%zu\n", st.separation,
			st.convergence, static_cast<int>(st.uv_draws), st.pin_uniform_q, st.z_driven_depth,
			static_cast<unsigned>(st.resolved.map), st.scenes.size());
		if (st.resolved.map == VR::ProfileDB::StereoMap::Log)
			std::printf("  log w0=%g w1=%g dfar=%g\n", st.resolved.log_w0, st.resolved.log_w1, st.resolved.log_dfar);
		const float qs[] = {1.0f / 1.0f, 1.0f / 2.3f, 1.0f / 16.0f, 1.0f / 98.0f, 0.0f};
		for (float q : qs)
			std::printf("    disparity at w=%-8g -> %.5f NDC\n", q > 0 ? 1.0f / q : INFINITY,
				VR::ProfileDB::EvalDisparity(st.resolved, st.separation, st.convergence, q));
	}
	std::printf("  screen: follow=%s distance=%s\n",
		p->screen_follow_head ? (*p->screen_follow_head ? "head" : "world") : "(setting)",
		p->screen_distance ? std::to_string(*p->screen_distance).c_str() : "(setting)");
	if (p->camera)
	{
		const auto& c = *p->camera;
		std::printf("  camera: guards=%zu writes=%zu matrix=%zu hooks=%zu base=%d padLook=%d\n", c.guards.size(),
			c.writes.size(), c.matrix_writes.size(), c.code_hooks.size(), c.base.has_value(), c.pad_look.has_value());
		for (const auto& w : c.writes)
			std::printf("    write 0x%08X rel=%d enc=%d src=%d compose=%d scale=%g bias=%g sign=%g clamp=[%g,%g]\n", w.ee_address,
				w.relative, static_cast<int>(w.encoding), static_cast<int>(w.source), static_cast<int>(w.compose),
				w.scale, w.bias, w.axis_sign, w.clamp_min, w.clamp_max);
		for (const auto& h : c.code_hooks)
			std::printf("    hook enabled=%d at 0x%08X tail 0x%08X cave 0x%08X scratch 0x%08X fpr=%u src=%d scale=%g\n",
				h.enabled, h.hook_address, h.tail_jump_address, h.cave_address, h.scratch_address, h.target_fpr,
				static_cast<int>(h.source), h.scale);
		for (const auto& g : c.guards)
			std::printf("    guard 0x%08X %s 0x%X width=%u\n", g.ee_address, g.not_equals ? "!=" : "==", g.equals, g.width);
		for (const auto& sl : c.silence)
			std::printf("    silence 0x%08X on=0x%08X off=0x%08X\n", sl.ee_address, sl.value_on, sl.value_off);
		for (const auto& m : c.matrix_writes)
			std::printf("    matrix 0x%08X anchored=%d order=%d\n", m.ee_address, m.anchored, static_cast<int>(m.order));
	}
	else
		std::printf("  camera: none\n");
	for (const auto& ctl : p->controls)
		std::printf("  control %s id=%s pad=%u usb=%d placement=(%g,%g,%g) rim=%g lock=%g binds=%zu\n",
			ctl.device.c_str(), ctl.id.c_str(), ctl.pad_port + 1, ctl.usb_port, ctl.placement.side,
			ctl.placement.height, ctl.placement.forward, ctl.rim_radius, ctl.lock_to_lock_deg, ctl.bind.size());
	return 0;
}
