# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
""" Tools for the agents. """


def get_product_info(product_type: str) -> str:
    """Get product information for the specified product type.

    Args:
        product_type: The type of product (speakers, displays, security)

    Returns:
        Product information string
    """
    products = {
        "speakers": "Smart Speaker Pro - Voice control, AI assistant, multi-room audio - $199",
        "displays": "Smart Display 10 - 10-inch touch screen, video calls, smart home hub - $399",
        "security": "Home Security System - 24/7 monitoring, mobile alerts, 4 cameras included - $599"
    }
    return products.get(product_type.lower(),
                        f"Product type '{product_type}' not found. Available: speakers, displays, security")


def check_device_status(device_name: str) -> str:
    """Check the status of a device for troubleshooting.

    Args:
        device_name: The name of the device to check

    Returns:
        Device status information
    """
    # Simulated device status check
    return f"Device '{device_name}' status: Online, Firmware: v2.1.0, Last sync: 2 minutes ago, All systems normal"
