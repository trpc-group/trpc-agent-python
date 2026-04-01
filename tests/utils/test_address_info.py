# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

import os
from unittest.mock import Mock
from unittest.mock import patch

import pytest
from trpc_agent_sdk.utils._address_info import get_current_ip
from trpc_agent_sdk.utils._address_info import get_ip_from_netifaces
from trpc_agent_sdk.utils._address_info import is_ip_valid


class TestIsIpValid:
    """Test suite for is_ip_valid function."""

    def test_valid_public_ipv4(self):
        """Test valid public IPv4 addresses."""
        assert is_ip_valid("8.8.8.8") is True
        assert is_ip_valid("1.1.1.1") is True
        assert is_ip_valid("203.0.113.1") is True

    def test_invalid_private_ipv4(self):
        """Test invalid private IPv4 addresses."""
        assert is_ip_valid("192.168.1.1") is False
        assert is_ip_valid("172.16.0.1") is False

    def test_invalid_loopback_ipv4(self):
        """Test invalid loopback IPv4 addresses."""
        assert is_ip_valid("127.0.0.1") is False
        assert is_ip_valid("127.1.1.1") is False

    def test_invalid_unspecified_ipv4(self):
        """Test invalid unspecified IPv4 addresses."""
        assert is_ip_valid("0.0.0.0") is False

    def test_invalid_link_local_ipv4(self):
        """Test invalid link-local IPv4 addresses."""
        assert is_ip_valid("169.254.1.1") is False

    def test_valid_public_ipv6(self):
        """Test valid public IPv6 addresses."""
        assert is_ip_valid("2001:db8::1") is True
        assert is_ip_valid("2001:0db8:85a3:0000:0000:8a2e:0370:7334") is True

    def test_invalid_loopback_ipv6(self):
        """Test invalid loopback IPv6 addresses."""
        assert is_ip_valid("::1") is False

    def test_invalid_unspecified_ipv6(self):
        """Test invalid unspecified IPv6 addresses."""
        assert is_ip_valid("::") is False

    def test_invalid_link_local_ipv6(self):
        """Test invalid link-local IPv6 addresses."""
        assert is_ip_valid("fe80::1") is False

    def test_invalid_unique_local_ipv6(self):
        """Test invalid unique local IPv6 addresses."""
        assert is_ip_valid("fc00::1") is False
        assert is_ip_valid("fec0::1") is False

    def test_invalid_ip_string(self):
        """Test invalid IP string."""
        assert is_ip_valid("not.an.ip") is False
        assert is_ip_valid("256.256.256.256") is False
        assert is_ip_valid("") is False
        assert is_ip_valid("invalid") is False


class TestGetIpFromNetifaces:
    """Test suite for get_ip_from_netifaces function."""

    @patch('trpc_agent_sdk.utils._address_info._get_ip_from_netifaces')
    @patch('trpc_agent_sdk.utils._address_info._get_ip_from_psutil')
    @patch('trpc_agent_sdk.utils._address_info._get_ip_fallback')
    def test_get_ip_from_netifaces_linux(self, mock_fallback, mock_psutil, mock_netifaces):
        """Test getting IP on Linux using netifaces."""
        mock_netifaces.return_value = "192.168.1.100"
        mock_psutil.return_value = ""
        mock_fallback.return_value = ""

        result = get_ip_from_netifaces()

        assert result == "192.168.1.100"
        mock_netifaces.assert_called_once()

    @patch('trpc_agent_sdk.utils._address_info._IS_WINDOWS', True)
    @patch('trpc_agent_sdk.utils._address_info._get_ip_from_psutil')
    @patch('trpc_agent_sdk.utils._address_info._get_ip_fallback')
    def test_get_ip_from_netifaces_windows(self, mock_fallback, mock_psutil):
        """Test getting IP on Windows using psutil."""
        mock_psutil.return_value = "10.0.0.1"
        mock_fallback.return_value = ""

        result = get_ip_from_netifaces()

        assert result == "10.0.0.1"
        mock_psutil.assert_called_once()

    @patch('trpc_agent_sdk.utils._address_info._get_ip_from_netifaces')
    @patch('trpc_agent_sdk.utils._address_info._get_ip_from_psutil')
    @patch('trpc_agent_sdk.utils._address_info._get_ip_fallback')
    def test_get_ip_from_netifaces_fallback(self, mock_fallback, mock_psutil, mock_netifaces):
        """Test getting IP using fallback method."""
        mock_netifaces.return_value = ""
        mock_psutil.return_value = ""
        mock_fallback.return_value = "203.0.113.1"

        result = get_ip_from_netifaces()

        assert result == "203.0.113.1"
        mock_fallback.assert_called_once()


class TestGetCurrentIp:
    """Test suite for get_current_ip function."""

    @patch('trpc_agent_sdk.utils._address_info.is_in_docker')
    @patch('trpc_agent_sdk.utils._address_info.get_ip_from_netifaces')
    def test_get_current_ip_not_in_docker(self, mock_get_ip, mock_is_docker):
        """Test getting IP when not in Docker."""
        mock_is_docker.return_value = False
        mock_get_ip.return_value = "203.0.113.1"

        result = get_current_ip()

        assert result == "203.0.113.1"
        mock_get_ip.assert_called_once()

    @patch('trpc_agent_sdk.utils._address_info.is_in_docker')
    @patch('trpc_agent_sdk.utils._address_info.get_ip_from_netifaces')
    def test_get_current_ip_in_docker_with_node_ip(self, mock_get_ip, mock_is_docker):
        """Test getting IP in Docker with NODE_IP environment variable."""
        mock_is_docker.return_value = True

        with patch.dict(os.environ, {"NODE_IP": "203.0.113.1"}):
            result = get_current_ip()

        assert result == "203.0.113.1"
        mock_get_ip.assert_not_called()

    @patch('trpc_agent_sdk.utils._address_info.is_in_docker')
    @patch('trpc_agent_sdk.utils._address_info.get_ip_from_netifaces')
    def test_get_current_ip_in_docker_invalid_node_ip(self, mock_get_ip, mock_is_docker):
        """Test getting IP in Docker with invalid NODE_IP."""
        mock_is_docker.return_value = True
        mock_get_ip.return_value = "203.0.113.1"

        with patch.dict(os.environ, {"NODE_IP": "192.168.1.1"}, clear=False):
            result = get_current_ip()

        assert result == "203.0.113.1"
        mock_get_ip.assert_called_once()

    @patch('trpc_agent_sdk.utils._address_info.is_in_docker')
    @patch('trpc_agent_sdk.utils._address_info.get_ip_from_netifaces')
    def test_get_current_ip_in_docker_no_node_ip(self, mock_get_ip, mock_is_docker):
        """Test getting IP in Docker without NODE_IP."""
        mock_is_docker.return_value = True
        mock_get_ip.return_value = "203.0.113.1"

        with patch.dict(os.environ, {}, clear=True):
            result = get_current_ip()

        assert result == "203.0.113.1"
        mock_get_ip.assert_called_once()
