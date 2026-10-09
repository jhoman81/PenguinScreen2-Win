// SPDX-FileCopyrightText: 2026 Patrick Carey <patrickfcarey@gmail.com>
// SPDX-License-Identifier: GPL-3.0

#pragma once

namespace VR::ProfileDB
{
	struct CameraPadLook;
}

namespace VR::CameraDriver
{
	struct PadLookState
	{
		int gate = 0;
		int latch = 0;
	};

	float PadLookDeflection(const ProfileDB::CameraPadLook& pl, float yaw_deg, PadLookState& st);

	void Apply();

	void RequestRecenter();

	// What the camera block is doing this vsync, for the compositor: whether it is armed, which
	// head axes it feeds into the game, and the recenter reference its angles are measured from.
	// The compositor uses it to hold a follow-head screen at the head pose the game's picture was
	// made with (screen.lagMs), on those axes only.
	struct HeadLookAxes
	{
		bool armed = false;
		bool yaw = false;
		bool pitch = false;
		bool roll = false;
		float ref_x = 0.0f, ref_y = 0.0f, ref_z = 0.0f, ref_w = 1.0f;
	};
	HeadLookAxes GetHeadLookAxes();

	void OnStateLoaded();

	bool SelfTestAssembler();

	bool SelfTestMath();
}
