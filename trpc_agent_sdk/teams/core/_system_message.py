# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""System message generation for TeamAgent leader.

This module provides functions to generate system messages that describe
the team structure and delegation instructions for the team leader.
"""

from __future__ import annotations

from typing import List


def generate_team_leader_system_message(
    team_name: str,
    team_instruction: str,
    members: List[dict],
) -> str:
    """Generate system message describing team structure for the leader.

    This creates a comprehensive system message that helps the team leader
    understand its role, available team members, and how to delegate tasks.

    Args:
        team_name: Name of the team.
        team_instruction: Base instruction for the team leader.
        members: List of member info dicts with 'name' and 'description'.

    Returns:
        Formatted system message string for the team leader.
    """
    parts: List[str] = []

    # Team name and role
    parts.append(f"You are the leader of team '{team_name}'.")

    # Team instruction
    if team_instruction:
        parts.append(f"\n{team_instruction}")

    # Member descriptions
    parts.append("\n## Team Members\n")
    parts.append("You have the following team members available:\n")

    for idx, member in enumerate(members, 1):
        member_name = member.get('name', f'member_{idx}')
        member_desc = member.get('description', 'No description provided')

        parts.append(f"### {idx}. {member_name}")
        parts.append(f"Description: {member_desc}")

        parts.append("")  # Empty line between members

    # Delegation instructions
    parts.append("## Delegation Instructions\n")

    parts.append("As the coordinator, you should:\n"
                 "1. Analyze the user's request to understand what needs to be done\n"
                 "2. Use `delegate_to_member(member_name, task)` to assign tasks to "
                 "appropriate team members\n"
                 "3. You can delegate to multiple members sequentially if needed\n"
                 "4. After receiving member responses, synthesize a final response for the user\n"
                 "\nTips:\n"
                 "- Be specific when describing tasks for members\n"
                 "- Consider member specializations when assigning tasks\n"
                 "- Combine insights from multiple members when appropriate")

    return "\n".join(parts)


def get_member_info_list(members) -> List[dict]:
    """Extract member information from agent instances.

    Args:
        members: List of agent instances inheriting from BaseAgent.

    Returns:
        List of dicts with member name, description, and tools.
    """
    member_info: List[dict] = []

    for member in members:
        info = {
            'name': member.name,
            'description': getattr(member, 'description', '') or 'No description',
        }

        # Extract tool names if available
        if hasattr(member, 'tools') and member.tools:
            tool_names = []
            for tool in member.tools:
                if hasattr(tool, 'name'):
                    tool_names.append(tool.name)
            info['tools'] = tool_names

        member_info.append(info)

    return member_info
