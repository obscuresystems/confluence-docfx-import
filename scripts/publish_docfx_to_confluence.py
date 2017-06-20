#!/usr/bin/python

"""
Script for publishing content from a generated DocFX web site to Confluence.
"""

import argparse
import codecs
import json
import lxml.html as html
import os
import requests
import urllib.parse as urlparse
import yaml

def main():
    """
    The main program entry-point.
    """

    args = parse_args()

    manifest = load_docfx_manifest(args.docfx_manifest)
    base_directory = os.path.dirname(args.docfx_manifest)
    docfx_mappings = load_docfx_xref_map(
        filename=os.path.join(base_directory, manifest["xrefmap"])
    )

    confluence_client = ConfluenceClient(args.confluence_address, args.confluence_user, args.confluence_password)
    confluence_mappings = get_confluence_mappings(confluence_client)

    docfx_uid_to_confluence_id = {
        entry["docfx_uid"] : entry["confluence_id"] for entry in confluence_mappings
    }
    docfx_href_to_confluence_id = {
        entry["docfx_href"].lstrip("/") : entry["confluence_id"] for entry in confluence_mappings
    }

    existing_mappings = []
    for mapping in docfx_mappings:
        docfx_uid = mapping["uid"]
        confluence_id = docfx_uid_to_confluence_id.get(docfx_uid)
        if confluence_id is None:
            print("WARNING: No mapping in Confluence for DocFX UID '{}'.".format(docfx_uid))

            continue

        mapping["confluence_id"] = confluence_id
        existing_mappings.append(mapping)

    new_mappings = [
        docfx_mapping for docfx_mapping in docfx_mappings
        if docfx_mapping["uid"] not in docfx_uid_to_confluence_id
    ]

    if new_mappings:
        print("Need to create {} new pages in confluence:".format(
            len(new_mappings)
        ))

        for mapping in new_mappings:
            mapping["title"] = "DocFX - {name} ({uid})".format(**mapping)
            print("\t{href} (UID='{uid}') => '{title}'".format(**mapping))

            confluence_id = confluence_client.create_page(
                space_key="TEST",
                title=mapping["title"],
                content="<h1>Placeholder</h1>\nThis page is a placeholder.",
                docfx_uid=mapping["uid"],
                docfx_href=mapping["href"]
            )
            mapping["confluence_id"] = confluence_id
            docfx_uid_to_confluence_id[mapping["uid"]] = confluence_id
            docfx_href_to_confluence_id[mapping["href"]] = confluence_id
            print("\t\tCreated:  {href} (UID='{uid}') => {confluence_id}".format(**mapping))
            existing_mappings.append(mapping)

    # Now update content for all pages.
    for mapping in existing_mappings:
        mapping["title"] = "DocFX - {name} ({uid})".format(**mapping)
        print("\t{href} (UID='{uid}') => '{title}'".format(**mapping))

        page_dir = os.path.dirname(mapping["href"].lstrip("/"))
        page_local_path = os.path.join(base_directory,
            mapping["href"].lstrip("/").replace("/", "\\")
        )
        with open(page_local_path) as page_content_file:
            page_content = '\n'.join((
                line.lstrip("\xef\xbb\xbf") for line in page_content_file.readlines()
            ))

            page_content = transform_links(page_dir, page_content, docfx_href_to_confluence_id)

        print("Updating Confluence page {}...".format(mapping["confluence_id"]))
        confluence_client.update_page(
            page_id=mapping["confluence_id"],
            title=mapping["title"],
            content=page_content,
            docfx_uid=mapping["uid"],
            docfx_href=mapping["href"]
        )
        print("\t\tUpdated: {href} (UID='{uid}') => {confluence_id}".format(**mapping))

def transform_links(base_dir, content, mappings):
    """
    Transform links in HTML content so they point to Confluence pages, etc.

    :param base_dir: The base directory for the content (all links are evaluated relative to this). The root is "", not "/".
    :param content: The HTML content.
    :param mappings: Mappings from link path (relative to root) to Confluence Id.
    :returns: The content, with links transformed.

    :type base_dir: str
    :type content: str
    :type mappings: dict
    :rtype: str
    """

    content_html = html.fragment_fromstring(content, create_parent="div")
    anchors = content_html.cssselect("a.xref")
    for anchor in anchors:
        href = anchor.attrib.get("href")
        if href is None:
            continue

        _, _, path, _, _, _ = urlparse.urlparse(href)

        # Remember - we'll be relative to some base directory.
        relative_path = base_dir + "/" + path.lstrip("/")

        page_id = mappings.get(relative_path)
        if page_id is None:
            print("WARNING - no mapping for xref link '{}'.".format(relative_path))

            continue

        anchor.attrib["href"] = href.replace(path, "/pages/viewpage.action?pageId={}".format(page_id))

    # Aaaand.. back to a regular string (since that's what we need to encode it in JSON).
    return codecs.decode(b"\n".join((
        html.tostring(element) for element in content_html.getchildren()
    )))

def get_confluence_mappings(confluence_client):
    """
    Retrieve existing page mappings from Confluence.abs

    :param confluence_client: The Confluence REST API client.
    :returns: A list of mappings (confluence_id, docfx_uid, docfx_href).
    :type confluence_client: ConfluenceClient
    :rtype: list
    """

    mappings = []

    step = 50
    uri_template="content?type=page&expand=metadata.properties.docfx&start={start}&limit={limit}"

    offset = 0
    while True:
        results = confluence_client.get_json(
            uri_template.format(start=offset, limit=step)
        )
        if results["size"] == 0:
            break # No more records.

        for result in results["results"]:
            properties = result["metadata"]["properties"]
            if "docfx" not in properties:
                continue # Page does not have DocFX properties.

            docfx_properties = properties["docfx"]["value"]["content"]

            mappings.append({
                "confluence_id": result["id"],
                "docfx_uid": docfx_properties["docfx_uid"],
                "docfx_href": docfx_properties["docfx_href"]
            })

        offset += step

    return mappings

def load_docfx_manifest(filename):
    """
    Load and parse a DocFX site manifest from the specified file.

    :param filename: The local file-system path of the file containing the DocFX site manifest.
    :returns: A dictionary containing the manifest.
    :rtype: dict
    """

    with open(filename) as docfx_manifest_file:
        return json.load(docfx_manifest_file)

def load_docfx_xref_map(filename):
    """
    Load and parse a DocFX cross-reference map from the specified file.

    :param filename: The local file-system path of the file containing the DocFX cross-reference map.
    :returns: A list containing the map entries.
    :rtype: list
    """

    with open(filename) as xref_map_file:
        return yaml.load(xref_map_file)["references"]

def parse_args():
    """
    Parse command-line arguments.

    :returns: The parsed arguments.
    """

    parser = argparse.ArgumentParser(__file__,
        description="Publish content from a generated DocFX web site to Confluence.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("--docfx-manifest",
        required=True,
        help="The local file-system path of manifest.json in the generated DocFX web site."
    )
    parser.add_argument("--confluence-space",
        required=True,
        help="The key (short name) of the target space in Confluence."
    )
    parser.add_argument("--confluence-address",
        default=os.getenv("CONFLUENCE_ADDR"),
        help="The base address of the Confluence server."
    )
    parser.add_argument("--confluence-user",
        default=os.getenv("CONFLUENCE_USER"),
        help="The user name for authentication to Confluence."
    )
    parser.add_argument("--confluence-password",
        default=os.getenv("CONFLUENCE_PASSWORD"),
        help="The password for authentication to Confluence."
    )
    args = parser.parse_args()

    if not args.confluence_address:
        parser.exit(status=1, message="Must specify address of Confluence server using --confluence-address argument or CONFLUENCE_ADDR environment variable.")

    if not args.confluence_user:
        parser.exit(status=1, message="Must specify user name for authentication to Confluence server using --confluence-user argument or CONFLUENCE_USER environment variable.")

    if not args.confluence_password:
        parser.exit(status=1, message="Must specify password for authentication to Confluence server using --confluence-password argument or CONFLUENCE_PASSWORD environment variable.")

    return args


class ConfluenceClient(object):
    """
    Simple client for the Confluence REST API.
    """

    def __init__(self, base_address, username, password):
        """
        Create a new ConfluenceClient.

        :param base_address: The base address of the Confluence REST API end-point.
        :param user: The user name for authenticating to Confluence.
        :param password: The password for authenticating to Confluence.
        :type base_address: str
        :type user: str
        :type password: str
        """

        self.base_address = base_address
        if not self.base_address.endswith("/rest/api/"):
            self.base_address = urlparse.urljoin(base_address, "rest/api/")

        self.session = requests.Session()
        self.session.auth = (username, password)
        self.session.headers["Accept"] = "application/json"
        self.session.headers["Content-Type"] = "application/json"

    def create_page(self, space_key, title, content, docfx_uid, docfx_href):
        """
        Create a new page in Confluence.

        :param space_key: The key (short name) of the target space in Confluence.
        :param title: The page title.
        :param content: The page content (raw HTML).
        :param docfx_uid: The page's associated DocFX UID.
        :param docfx_href: The page's URL in the generated DocFX web site.
        :returns: The new page Id.

        :type space_key: str
        :type title: str
        :type content: str
        :type docfx_uid: str
        :type docfx_href: str
        :rtype: int
        """

        # TODO: Work out the best way to preserve the site's page hierarchy in Confluence.

        # Create page with raw content (URLs in the HTML are modified in a separate step)
        response = self.post_json("content", data={
            "type": "page",
            "title": title,
            "space": {
                "key": space_key
            },
            "body": {
                "storage": {
                    "value": content,
                    "representation": "storage"
                }
            }
        })

        if "id" not in response:
            raise Exception(response["message"])

        page_id = response["id"]

        # Attach DocFX metadata.
        property_url = "content/{}/property".format(page_id)
        response = self.post_json(property_url, data={
            "key": "docfx",
            "value": {
                "description": "DocFX page properties",
                "content": {
                    "docfx_uid": docfx_uid,
                    "docfx_href": docfx_href
                }
            }
        })

        return page_id

    def update_page(self, page_id, title, content, docfx_uid, docfx_href):
        """
        Update an existing page in Confluence.

        :param page_id: The Id of the target page in Confluence.
        :param title: The page title.
        :param content: The page content (raw HTML).
        :param docfx_uid: The page's associated DocFX UID.
        :param docfx_href: The page's URL in the generated DocFX web site.

        :type page_id: int
        :type title: str
        :type content: str
        :type docfx_uid: str
        :type docfx_href: str
        """

        # TODO: Work out the best way to preserve the site's page hierarchy in Confluence.

        # Get page version.
        page_url = "content/{}".format(page_id)

        response = self.get_json(page_url)
        if "id" not in response:
            raise Exception(response["message"])

        page = response
        page_version = page["version"]["number"]

        # Create page with raw content (URLs in the HTML are modified in a separate step)
        response = self.put_json(page_url, data={
            "id": str(page_id),
            "type": "page",
            "title": title,
            "space": {
                "key": page["space"]["key"]
            },
            "body": {
                "storage": {
                    "value": content,
                    "representation": "storage"
                }
            },
            "version": {
                "number": page_version + 1
            }
        })

        if "id" not in response:
            raise Exception(response["message"])

        # Update DocFX metadata.
        property_url = "content/{}/property/docfx".format(page_id)
        response = self.delete_json(property_url)
        if "message" in response:
            raise Exception(response["message"])

        property_url = "content/{}/property".format(page_id)
        response = self.post_json(property_url, data={
            "key": "docfx",
            "value": {
                "description": "DocFX page properties",
                "content": {
                    "docfx_uid": docfx_uid,
                    "docfx_href": docfx_href
                }
            }
        })

        return page_id

    def get_json(self, relative_url, **kwargs):
        """
        Perform an HTTP GET, and return the result as JSON.

        :param relative_url: The target URL (relative to the base address).
        """

        target_url = urlparse.urljoin(self.base_address, relative_url)
        response = self.session.get(target_url, *kwargs)

        return response.json()

    def post_json(self, relative_url, data, **kwargs):
        """
        Perform an HTTP POST, and return the result as JSON.

        :param relative_url: The target URL (relative to the base address).
        :param data: The request body.
        """

        target_url = urlparse.urljoin(self.base_address, relative_url)
        if data is not str:
            data = json.dumps(data)

        response = self.session.post(target_url, data, *kwargs)

        return response.json()

    def put_json(self, relative_url, data, **kwargs):
        """
        Perform an HTTP PUT, and return the result as JSON.

        :param relative_url: The target URL (relative to the base address).
        :param data: The request body.
        """

        target_url = urlparse.urljoin(self.base_address, relative_url)
        if data is not str:
            data = json.dumps(data)

        response = self.session.put(target_url, data, *kwargs)

        return response.json()

    def delete_json(self, relative_url, **kwargs):
        """
        Perform an HTTP POST, and return the result as JSON.

        :param relative_url: The target URL (relative to the base address).
        """

        target_url = urlparse.urljoin(self.base_address, relative_url)
        response = self.session.delete(target_url, *kwargs)

        if response.text:
            return response.json()

        return {}

if __name__ == "__main__":
    main()