#!/usr/bin/env python3
"""
Google GSC + GA4 MCP Server
A simple server that lets Claude talk to your Search Console and Analytics data.
"""

import os
import json
import base64
from google.oauth2 import service_account
from googleapiclient.discovery import build
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import RunReportRequest, DateRange, Metric, Dimension
from google.analytics.admin_v1alpha import AnalyticsAdminServiceClient
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from mcp.server.sse import SseServerTransport
from starlette.routing import Route

# ========== SETUP ==========
# Read the service account JSON from environment variable
SCOPES = [
    'https://www.googleapis.com/auth/webmasters.readonly',
    'https://www.googleapis.com/auth/analytics.readonly',
    'https://www.googleapis.com/auth/analytics.manage.readonly'
]

def get_credentials():
    """Create credentials from the service account JSON stored in env."""
    creds_b64 = os.environ.get("GOOGLE_CREDENTIALS_B64")
    if not creds_b64:
        raise ValueError("Missing GOOGLE_CREDENTIALS_B64 environment variable!")
    
    creds_json = base64.b64decode(creds_b64).decode('utf-8')
    creds_info = json.loads(creds_json)
    credentials = service_account.Credentials.from_service_account_info(
        creds_info, scopes=SCOPES
    )
    return credentials

# Initialize MCP
mcp = FastMCP("google-gsc-ga4-server")

# ========== GSC TOOLS ==========

@mcp.tool()
async def gsc_list_properties() -> str:
    """List all Google Search Console properties you have access to."""
    try:
        creds = get_credentials()
        service = build('webmasters', 'v3', credentials=creds)
        sites = service.sites().list().execute()
        site_list = sites.get('siteEntry', [])
        
        if not site_list:
            return "No GSC properties found. Make sure you added the service account to your GSC properties."
        
        result = "🏠 Your GSC Properties:\n\n"
        for site in site_list:
            result += f"• {site['siteUrl']} (Permission: {site['permissionLevel']})\n"
        return result
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool()
async def gsc_get_search_analytics(site_url: str, days: int = 28, row_limit: int = 10) -> str:
    """
    Get search analytics data for a specific site.
    
    Args:
        site_url: The full site URL (e.g., https://example.com/)
        days: Number of days to look back (default 28)
        row_limit: Number of top queries to return (default 10, max 50)
    """
    try:
        creds = get_credentials()
        service = build('webmasters', 'v3', credentials=creds)
        
        from datetime import datetime, timedelta
        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        
        request = {
            'startDate': start_date,
            'endDate': end_date,
            'dimensions': ['query'],
            'rowLimit': min(row_limit, 50)
        }
        
        response = service.searchanalytics().query(siteUrl=site_url, body=request).execute()
        rows = response.get('rows', [])
        
        if not rows:
            return f"No search data found for {site_url} in the last {days} days."
        
        result = f"🔍 Top Queries for {site_url} (Last {days} days)\n\n"
        result += f"{'Query':<40} {'Clicks':<10} {'Impressions':<12} {'CTR':<8} {'Position':<8}\n"
        result += "-" * 85 + "\n"
        
        for row in rows:
            query = row['keys'][0][:38]
            clicks = row.get('clicks', 0)
            impressions = row.get('impressions', 0)
            ctr = f"{row.get('ctr', 0)*100:.1f}%"
            position = f"{row.get('position', 0):.1f}"
            result += f"{query:<40} {clicks:<10} {impressions:<12} {ctr:<8} {position:<8}\n"
        
        return result
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool()
async def gsc_get_top_pages(site_url: str, days: int = 28, row_limit: int = 10) -> str:
    """
    Get top pages by clicks from Google Search Console.
    
    Args:
        site_url: The full site URL
        days: Number of days to look back
        row_limit: Number of pages to return
    """
    try:
        creds = get_credentials()
        service = build('webmasters', 'v3', credentials=creds)
        
        from datetime import datetime, timedelta
        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        
        request = {
            'startDate': start_date,
            'endDate': end_date,
            'dimensions': ['page'],
            'rowLimit': min(row_limit, 50)
        }
        
        response = service.searchanalytics().query(siteUrl=site_url, body=request).execute()
        rows = response.get('rows', [])
        
        if not rows:
            return f"No page data found for {site_url}."
        
        result = f"📄 Top Pages for {site_url} (Last {days} days)\n\n"
        result += f"{'Page':<50} {'Clicks':<10} {'Impressions':<12}\n"
        result += "-" * 75 + "\n"
        
        for row in rows:
            page = row['keys'][0][:48]
            clicks = row.get('clicks', 0)
            impressions = row.get('impressions', 0)
            result += f"{page:<50} {clicks:<10} {impressions:<12}\n"
        
        return result
    except Exception as e:
        return f"Error: {str(e)}"

# ========== GA4 TOOLS ==========

@mcp.tool()
async def ga4_list_properties() -> str:
    """List all Google Analytics 4 properties you have access to."""
    try:
        creds = get_credentials()
        client = AnalyticsAdminServiceClient(credentials=creds)
        accounts = client.list_accounts()
        
        result = "📊 Your GA4 Properties:\n\n"
        count = 0
        
        for account in accounts:
            properties = client.list_properties(filter=f"parent:{account.name}")
            for prop in properties:
                count += 1
                result += f"• {prop.display_name} (ID: {prop.name.split('/')[-1]})\n"
        
        if count == 0:
            return "No GA4 properties found. Make sure the service account has access."
        return result
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool()
async def ga4_run_report(property_id: str, days: int = 30, metrics: str = "activeUsers,sessions", dimensions: str = "date") -> str:
    """
    Run a Google Analytics 4 report.
    
    Args:
        property_id: Your GA4 Property ID (numbers only, e.g., 123456789)
        days: Number of days to look back
        metrics: Comma-separated metrics (e.g., activeUsers,sessions,bounceRate)
        dimensions: Comma-separated dimensions (e.g., date,country,deviceCategory)
    """
    try:
        creds = get_credentials()
        client = BetaAnalyticsDataClient(credentials=creds)
        
        from datetime import datetime, timedelta
        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        
        metric_list = [Metric(name=m.strip()) for m in metrics.split(',')]
        dimension_list = [Dimension(name=d.strip()) for d in dimensions.split(',')]
        
        request = RunReportRequest(
            property=f"properties/{property_id}",
            date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
            metrics=metric_list,
            dimensions=dimension_list,
            limit=50
        )
        
        response = client.run_report(request)
        
        if not response.rows:
            return "No data returned for this report."
        
        # Build header
        headers = [d.name for d in dimension_list] + [m.name for m in metric_list]
        result = "📈 GA4 Report\n\n"
        result += " | ".join(headers) + "\n"
        result += "-" * (len(" | ".join(headers)) + 10) + "\n"
        
        for row in response.rows:
            values = [dim.value for dim in row.dimension_values] + [met.value for met in row.metric_values]
            result += " | ".join(values) + "\n"
        
        return result
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool()
async def ga4_top_countries(property_id: str, days: int = 30) -> str:
    """
    Get top countries by active users from GA4.
    
    Args:
        property_id: Your GA4 Property ID
        days: Number of days to look back
    """
    try:
        return await ga4_run_report(
            property_id=property_id,
            days=days,
            metrics="activeUsers,sessions",
            dimensions="country"
        )
    except Exception as e:
        return f"Error: {str(e)}"

# ========== HTTP SERVER SETUP (for Render) ==========

def create_app():
    """Create the Starlette app with SSE transport for Render hosting."""
    transport = SseServerTransport("/messages/")
    
    async def handle_sse(request):
        async with transport.connect_sse(
            request.scope, request.receive, request._send
        ) as (read_stream, write_stream):
            await mcp.run(
                read_stream,
                write_stream,
                mcp.create_initialization_options(),
            )
    
    return Starlette(
        debug=True,
        routes=[
            Route("/sse", endpoint=handle_sse),
            Route("/messages/", endpoint=transport.handle_post_message, methods=["POST"]),
        ],
    )

app = create_app()

# For local testing with stdio (optional)
if __name__ == "__main__":
    import asyncio
    mcp.run()
