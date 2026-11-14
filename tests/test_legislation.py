"""legislation.gov.uk client: XML parsing and every status of fetch_provision, with HTTP recorded."""
import pytest

from app.sources import legislation as leg

XML = """<?xml version="1.0" encoding="UTF-8"?>
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation"
             xmlns:ukm="http://www.legislation.gov.uk/namespaces/metadata"
             xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:atom="http://www.w3.org/2005/Atom">
  <ukm:Metadata>
    <dc:title>Freedom of Information Act 2000</dc:title>
    <atom:link rel="http://purl.org/dc/terms/hasVersion" title="2005-01-01"/>
    <atom:link rel="http://purl.org/dc/terms/hasVersion" title="current"/>
  </ukm:Metadata>
  <Primary><Body>
    <P1group RestrictStartDate="2005-01-01">
      <Title>Time for   compliance with request</Title>
      <P1><Pnumber>10</Pnumber><P1para>
        <P2><Pnumber>1</Pnumber><P2para>
          <Text>A public authority must comply with section 1(1) promptly and in any event not later than the
            <CommentaryRef Ref="c1"/>twentieth working day following the date of receipt.</Text>
        </P2para></P2>
        <P2><Pnumber>6</Pnumber><P2para>
          <Text>In this section "working day" means any day other than a bank holiday in any part of the United Kingdom.</Text>
        </P2para></P2>
      </P1para></P1>
    </P1group>
  </Body></Primary>
</Legislation>"""


def test_parse_provision_xml_text_heading_and_versions():
    d = leg.parse_provision_xml(XML)
    assert d["act_title"] == "Freedom of Information Act 2000"
    assert d["heading"] == "Time for compliance with request"
    assert d["version_start"] == "2005-01-01" and "2005-01-01" in d["versions"]
    lines = d["text"].splitlines()
    assert lines[0] == "  (1) A public authority must comply with section 1(1) promptly and in any event not later " \
                       "than the twentieth working day following the date of receipt."
    assert lines[1].startswith("  (6) In this section")
    assert "CommentaryRef" not in d["text"] and "c1" not in d["text"]


@pytest.fixture
def web(monkeypatch):
    """Map URL -> HTTP status; 200 serves XML. Records every URL asked for."""
    routes, asked = {}, []

    def get(url, **kw):
        asked.append(url)
        status = routes.get(url, 404)
        return {"status": status, "body": XML if status == 200 else "", "url": url}

    monkeypatch.setattr(leg.http, "get", get)
    return routes, asked


U = "https://www.legislation.gov.uk/ukpga/2000/36/section"


def test_fetch_ok(web):
    routes, asked = web
    routes[f"{U}/10/data.xml"] = 200
    r = leg.fetch_provision("ukpga", "2000", "36", "section", "10")
    assert r["status"] == "ok" and r["url"] == f"{U}/10" and "twentieth working day" in r["text"]
    assert asked == [f"{U}/10/data.xml"]


def test_fetch_retries_with_trailing_dot(web):
    routes, _ = web
    routes["https://www.legislation.gov.uk/eur/2016/679/article/12A./data.xml"] = 200
    r = leg.fetch_provision("eur", "2016", "679", "article", "12A")
    assert r["status"] == "ok" and r["url"].endswith("/article/12A.")


def test_missing_section_missing_act_and_network_error(web):
    routes, _ = web
    assert leg.fetch_provision("ukpga", "2000", "36", "section", "64Z")["status"] == "provision_not_found"
    routes[f"{U}/1/data.xml"] = 400
    assert leg.fetch_provision("ukpga", "2000", "36", "section", "1")["status"] == "act_not_found"
    routes[f"{U}/2/data.xml"] = 0
    assert leg.fetch_provision("ukpga", "2000", "36", "section", "2")["status"] == "error"


def test_point_in_time_falls_back_to_current_and_says_so(web):
    routes, _ = web
    routes[f"{U}/10/data.xml"] = 200            # no version for 2001-01-01, only the current one
    r = leg.fetch_provision("ukpga", "2000", "36", "section", "10", as_of="2001-01-01")
    assert r["status"] == "not_in_force_on_date" and r["as_of"] == ""


def test_act_exists(monkeypatch):
    monkeypatch.setattr(leg.http, "get", lambda url, **kw: {
        "status": 200, "body": "<html><title>\n Freedom of Information Act 2000 \n</title></html>", "url": url})
    r = leg.act_exists("ukpga", "2000", "36")
    assert r["exists"] and r["page_title"] == "Freedom of Information Act 2000"
    monkeypatch.setattr(leg.http, "get", lambda url, **kw: {"status": 400, "body": "", "url": url})
    assert not leg.act_exists("ukpga", "2019", "999")["exists"]
