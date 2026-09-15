# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Credential provider plugin model.

Storm-conformant brokers ship the four base provider classes:
  - BearerTokenProvider — covers ~70% SaaS
  - BasicAuthProvider — Bitbucket, internal SaaS
  - OAuth2RefreshFlowProvider — Salesforce, Google Workspace, Microsoft Graph, Atlassian Cloud OAuth
  - RequestSignerProvider — AWS SigV4, custom HMAC

Plus three specialized:
  - DBConnectionBroker — DB connections via SCM_RIGHTS
  - KeyHandleProvider — sign-only access to crypto keys
  - CookieJarProvider — cookie-based legacy SaaS

Each provider is responsible for ONE thing: prepare an authenticated
request (or signed payload) on behalf of an agent without ever
revealing the underlying secret to the agent.
"""

from .base import Provider, ProviderResult, ProviderError
from .bearer_token import BearerTokenProvider
from .basic_auth import BasicAuthProvider
from .oauth2_refresh import OAuth2RefreshFlowProvider
from .request_signer import RequestSignerProvider
from .aws import AwsSigV4Provider
from .anthropic import AnthropicProvider
from .openai import OpenAIProvider
from .github import GitHubProvider
from .slack import SlackProvider
from .jira import JiraProvider
from .stripe import StripeProvider
from .sendgrid import SendGridProvider
from .twilio import TwilioProvider
from .bitbucket import BitbucketProvider
from .salesforce import SalesforceProvider
from .google import GoogleOAuth2Provider
from .microsoft import MicrosoftGraphProvider
from .atlassian import AtlassianOAuth2Provider
from .key_handle import KeyHandleProvider
from .db_connection import DBConnectionBrokerProvider

__all__ = [
    "Provider",
    "ProviderResult",
    "ProviderError",
    "BearerTokenProvider",
    "BasicAuthProvider",
    "OAuth2RefreshFlowProvider",
    "RequestSignerProvider",
    "AwsSigV4Provider",
    "AnthropicProvider",
    "OpenAIProvider",
    "GitHubProvider",
    "SlackProvider",
    "JiraProvider",
    "StripeProvider",
    "SendGridProvider",
    "TwilioProvider",
    "BitbucketProvider",
    "SalesforceProvider",
    "GoogleOAuth2Provider",
    "MicrosoftGraphProvider",
    "AtlassianOAuth2Provider",
    "KeyHandleProvider",
    "DBConnectionBrokerProvider",
]
