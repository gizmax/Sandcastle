# Search Console Setup Instructions

## 1. Google Search Console

1. Go to https://search.google.com/search-console
2. Click "Add property"
3. Choose "URL prefix" -> enter `https://sandcastle-ai.eu`
4. Verification method: "HTML tag" (easiest)
   - Copy the meta tag Google gives you
   - Add it to `site/index.html` in the `<head>` section
   - Upload via FTP (credentials from env, never commit them): `curl -T site/index.html --user "$SANDCASTLE_FTP_USER:$SANDCASTLE_FTP_PASS" "ftp://ftp.sandcastle-ai.eu/public_html/index.html"`
   - Click "Verify" in Google
5. After verification:
   - Go to "Sitemaps" in left menu
   - Submit: `https://sandcastle-ai.eu/sitemap.xml`
   - Wait 24-48h for initial indexing

## 2. Bing Webmaster Tools

1. Go to https://www.bing.com/webmasters
2. Click "Add your site"
3. Enter `https://sandcastle-ai.eu`
4. Choose "Import from Google Search Console" (fastest if GSC is set up first)
   - OR use "HTML tag" verification like Google
5. After verification:
   - Submit sitemap: `https://sandcastle-ai.eu/sitemap.xml`

## 3. Manual Indexing Requests

After verification, request indexing of key pages:

**Google:** In Search Console -> URL Inspection -> paste URL -> "Request Indexing"
- https://sandcastle-ai.eu
- https://sandcastle-ai.eu/hub
- https://sandcastle-ai.eu/pricing
- https://sandcastle-ai.eu/security

**Bing:** In Webmaster Tools -> URL Submission -> paste URLs

## 4. Verify Indexing (check after 48h)

Search in Google: `site:sandcastle-ai.eu`
Search in Bing: `site:sandcastle-ai.eu`

Expected: at least 4 pages indexed within a week.
