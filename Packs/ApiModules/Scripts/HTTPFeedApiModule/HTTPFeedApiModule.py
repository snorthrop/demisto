import demistomock as demisto
from CommonServerPython import *
from CommonServerUserPython import *

""" IMPORTS """
import urllib3
import requests
from typing import Optional, Pattern, List
from ipaddress import ip_address, summarize_address_range

# disable insecure warnings
urllib3.disable_warnings()

""" GLOBALS """
TAGS = "tags"
TLP_COLOR = "trafficlightprotocol"
DATE_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
THRESHOLD_IN_SECONDS = 43200  # 12 hours in seconds
IP_RANGE_REGEX_PATTERN = (
    r"((?:\d{1,3}\.){3}\d{1,3}|(?:[a-fA-F0-9]{1,4}(?::[a-fA-F0-9]{0,4}){0,6}::?[a-fA-F0-9]{0,4}))"
    r"\s*-\s*((?:\d{1,3}\.){3}\d{1,3}|(?:[a-fA-F0-9]{1,4}(?::[a-fA-F0-9]{0,4}){0,6}::?[a-fA-F0-9]{0,4}))"
)


class Client(BaseClient):
    def __init__(
        self,
        url: str,
        feed_name: str = "http",
        insecure: bool = False,
        credentials: dict = None,
        ignore_regex: str = None,
        encoding: str = None,
        indicator_type: str = "",
        indicator: str = "",
        fields: str = "{}",
        feed_url_to_config: dict = None,
        polling_timeout: int = 20,
        headers: dict = None,
        proxy: bool = False,
        custom_fields_mapping: dict = None,
        **kwargs,
    ):
        """Implements class for miners of plain text feeds over HTTP.
        **Config parameters**
        :param: url: URL of the feed.
        :param: polling_timeout: timeout of the polling request in seconds.
            Default: 20
        :param feed_name: The name of the feed.
        :param: custom_fields_mapping: Dict, the "fields" to be used in the indicator - where the keys
        are the *current* keys of the fields returned feed data and the *values* are the *indicator fields in Demisto*.
        :param: headers: dict, Optional list of headers to send in the request.
        :param: ignore_regex: Python regular expression for lines that should be
            ignored. Default: *null*
        :param: insecure: boolean, if *false* feed HTTPS server certificate is
            verified. Default: *false*
        :param credentials: username and password used for basic authentication.
                            Can be also used as API key header and value by specifying _header in the username field.
        :param: encoding: encoding of the feed, if not UTF-8. See
            ``str.decode`` for options. Default: *null*, meaning do
            nothing, (Assumes UTF-8).
        :param: indicator_type: Default indicator type
        :param: indicator: an *extraction dictionary* to extract the indicator from
            the line. If *null*, the text until the first whitespace or newline
            character is used as indicator. Default: *null*
        :param: fields: a dictionary of *extraction dictionaries* to extract
            additional attributes from each line. Default: {}
        :param: feed_url_to_config: For each service, a dictionary to process indicators by.
        For example, ASN feed:
        'https://www.spamhaus.org/drop/asndrop.txt': {
            'indicator_type': ASN,
            'indicator': { (Regex to extract the indicator by, if empty - the whole line is extracted)
                'regex': r'^AS[0-9]+',
            },
            'fields': [{ (See Extraction dictionary below)
                'asndrop_country': {
                    'regex': '^.*;\\W([a-zA-Z]+)\\W+',
                    'transform: r'\1'
                }
            }]
        }
        :param: proxy: Use proxy in requests.
        **Extraction dictionary**
            Extraction dictionaries contain the following keys:
            :regex: Python regular expression for searching the text.
            :transform: template to generate the final value from the result
                of the regular expression. Default: the entire match of the regex
                is used as extracted value.
            See Python `re <https://docs.python.org/2/library/re.html>`_ module for
            details about Python regular expressions and templates.
        Example:
            Example config in YAML where extraction dictionaries are used to
            extract the indicator and additional fields::
                url: https://www.dshield.org/block.txt
                ignore_regex: "[#S].*"
                indicator:
                    regex: '^([0-9]{1,3}\\.[0-9]{1,3}\\.[0-9]{1,3}\\.[0-9]{1,3})\\t([0-9]
                    {1,3}\\.[0-9]{1,3}\\.[0-9]{1,3}\\.[0-9]{1,3})'
                    transform: '\\1-\\2'
                fields:
                    dshield_nattacks:
                        regex: '^.*\\t.*\\t[0-9]+\\t([0-9]+)'
                        transform: '\\1'
                    dshield_name:
                        regex: '^.*\\t.*\\t[0-9]+\\t[0-9]+\\t([^\\t]+)'
                        transform: '\\1'
                    dshield_country:
                        regex: '^.*\\t.*\\t[0-9]+\\t[0-9]+\\t[^\\t]+\\t([A-Z]+)'
                        transform: '\\1'
                    dshield_email:
                        regex: '^.*\\t.*\\t[0-9]+\\t[0-9]+\\t[^\\t]+\\t[A-Z]+\\t(\\S+)'
                        transform: '\\1'
            Example config in YAML where the text in each line until the first
            whitespace is used as indicator::
                url: https://ransomwaretracker.abuse.ch/downloads/CW_C2_URLBL.txt
                ignore_regex: '^#'
        """
        super().__init__(base_url=url, verify=not insecure, proxy=proxy)
        handle_proxy()
        try:
            self.polling_timeout = int(polling_timeout)
        except (ValueError, TypeError):
            raise ValueError('Please provide an integer value for "Request Timeout"')

        self.headers = headers
        self.encoding = encoding
        self.feed_name = feed_name
        if not credentials:
            credentials = {}
        self.username = None
        self.password = None

        username = credentials.get("identifier", "")
        if username.startswith("_header:"):
            if not self.headers:
                self.headers = {}
            header_field = username.split(":")
            if len(header_field) < 2:
                raise ValueError(
                    'An incorrect value was provided for an API key header. The correct value is "_header:<header_name>"'
                )
            header_name: str = header_field[1]
            header_value: str = credentials.get("password", "")
            self.headers[header_name] = header_value
        else:
            self.username = username
            self.password = credentials.get("password", None)

        self.indicator_type = indicator_type
        if feed_url_to_config:
            self.feed_url_to_config = feed_url_to_config
        else:
            self.feed_url_to_config = {url: self.get_feed_config(fields, indicator)}
        self.ignore_regex: Optional[Pattern] = None
        if ignore_regex is not None:
            self.ignore_regex = re.compile(ignore_regex)

        if custom_fields_mapping is None:
            custom_fields_mapping = {}
        self.custom_fields_mapping = custom_fields_mapping

    def get_feed_config(self, fields_json: str = "", indicator_json: str = ""):
        """
        Get the feed configuration from the indicator and field JSON strings.
        :param fields_json: JSON string of fields to extract, for example:
            {
                'fieldname': {
                    'regex': regex,
                    'transform': r'\1'
                }
            },
            {
                'asndrop_org': {
                    'regex': regex,
                    'transform': r'\1'
                }
            }
        :param indicator_json: JSON string of the indicator to extract, for example:
            {'regex': regex}
        :return: The feed configuration.
        """
        config = {}
        if indicator_json:
            indicator = json.loads(indicator_json)
            if "regex" in indicator:
                indicator["regex"] = re.compile(indicator["regex"])
            else:
                raise ValueError(f"{self.feed_name} - indicator stanza should have a regex")
            if "transform" not in indicator:
                if indicator["regex"].groups > 0:
                    LOG(f"{self.feed_name} - no transform string for indicator but pattern contains groups")
                indicator["transform"] = r"\g<0>"

            config["indicator"] = indicator
        if fields_json:
            fields = json.loads(fields_json)
            config["fields"] = []
            for f, fattrs in fields.items():
                if "regex" in fattrs:
                    fattrs["regex"] = re.compile(fattrs["regex"])
                else:
                    raise ValueError(f"{self.feed_name} - {f} field does not have a regex")
                if "transform" not in fattrs:
                    if fattrs["regex"].groups > 0:
                        LOG(f"{self.feed_name} - no transform string for field {f} but pattern contains groups")
                    fattrs["transform"] = r"\g<0>"
                config["fields"].append({f: fattrs})

        return config

    def build_iterator(self, **kwargs):
        """
        For each URL (service), send an HTTP request to get indicators and return them after filtering by Regex
        :param kwargs: Arguments to send to the HTTP API endpoint
        :return: List of indicators
        """
        kwargs["stream"] = True
        kwargs["verify"] = self._verify
        kwargs["timeout"] = self.polling_timeout

        if self.headers is not None:
            kwargs["headers"] = self.headers

        if self.username is not None and self.password is not None:
            kwargs["auth"] = (self.username, self.password)
        try:
            urls = self._base_url
            url_to_response_list: List[dict] = []
            if not isinstance(urls, list):
                urls = [urls]
            for url in urls:
                if is_demisto_version_ge("6.5.0"):
                    # Set the If-None-Match and If-Modified-Since headers if we have etag or
                    # last_modified values in the context, for server version higher than 6.5.0.
                    last_run = demisto.getLastRun()
                    etag = last_run.get(url, {}).get("etag")
                    if etag:
                        etag = etag.strip('"')
                    last_modified = last_run.get(url, {}).get("last_modified")
                    last_updated = last_run.get(url, {}).get("last_updated")
                    # To avoid issues with indicators expiring, if 'last_updated' is over X hours old,
                    # we'll refresh the indicators to ensure their expiration time is updated.
                    # For further details, refer to : https://confluence-dc.paloaltonetworks.com/display/DemistoContent/Json+Api+Module     # noqa: E501
                    if last_updated and has_passed_time_threshold(
                        timestamp_str=last_updated, seconds_threshold=THRESHOLD_IN_SECONDS
                    ):
                        last_modified = None
                        etag = None
                        demisto.debug(
                            "Since it's been a long time with no update, to make sure we are keeping the indicators\
                            alive, we will refetch them from scratch"
                        )
                    if etag:
                        if not kwargs.get("headers"):
                            kwargs["headers"] = {}
                        kwargs["headers"]["If-None-Match"] = etag

                    if last_modified:
                        if not kwargs.get("headers"):
                            kwargs["headers"] = {}
                        kwargs["headers"]["If-Modified-Since"] = last_modified

                r = requests.get(url, **kwargs)
                try:
                    r.raise_for_status()
                except Exception:
                    LOG(f"{self.feed_name!r} - exception in request: {r.status_code!r} {r.content!r}")
                    raise
                no_update = get_no_update_value(r, url) if is_demisto_version_ge("6.5.0") else True
                url_to_response_list.append({url: {"response": r, "no_update": no_update}})
        except requests.exceptions.ConnectTimeout as exception:
            err_msg = (
                "Connection Timeout Error - potential reasons might be that the Server URL parameter"
                " is incorrect or that the Server is not accessible from your host."
            )
            raise DemistoException(err_msg, exception)
        except requests.exceptions.SSLError as exception:
            # in case the "Trust any certificate" is already checked
            if not self._verify:
                raise
            err_msg = (
                "SSL Certificate Verification Failed - try selecting 'Trust any certificate' checkbox in"
                " the integration configuration."
            )
            raise DemistoException(err_msg, exception)
        except requests.exceptions.ProxyError as exception:
            err_msg = (
                "Proxy Error - if the 'Use system proxy' checkbox in the integration configuration is"
                " selected, try clearing the checkbox."
            )
            raise DemistoException(err_msg, exception)
        except requests.exceptions.ConnectionError as exception:
            # Get originating Exception in Exception chain
            error_class = str(exception.__class__)
            err_type = "<" + error_class[error_class.find("'") + 1 : error_class.rfind("'")] + ">"
            err_msg = (
                "Verify that the server URL parameter"
                " is correct and that you have access to the server from your host."
                "\nError Type: {}\nError Number: [{}]\nMessage: {}\n".format(err_type, exception.errno, exception.strerror)
            )
            raise DemistoException(err_msg, exception)

        results = []
        for url_to_response in url_to_response_list:
            for url, res_data in url_to_response.items():
                lines = res_data.get("response")
                result = lines.iter_lines()
                if self.encoding is not None:
                    result = (x.decode(self.encoding).encode("utf_8") for x in result)
                else:
                    result = (x.decode("utf_8") for x in result)
                if self.ignore_regex is not None:
                    result = filter(
                        lambda x: self.ignore_regex.match(x) is None,  # type: ignore[union-attr, arg-type]
                        result,
                    )
                results.append({url: {"result": result, "no_update": res_data.get("no_update")}})
        return results

    def custom_fields_creator(self, attributes: dict):
        created_custom_fields = {}
        for attribute in attributes:
            if attribute in self.custom_fields_mapping or attribute in [TAGS, TLP_COLOR]:
                if attribute in [TAGS, TLP_COLOR]:
                    created_custom_fields[attribute] = attributes[attribute]
                else:
                    created_custom_fields[self.custom_fields_mapping[attribute]] = attributes[attribute]

        return created_custom_fields


def get_no_update_value(response: requests.Response, url: str) -> bool:
    """
    detect if the feed response has been modified according to the headers etag and last_modified.
    For more information, see this:
    https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Last-Modified
    https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/ETag
    Args:
        response: (requests.Response) The feed response.
        url: (str) The feed URL (service).
    Returns:
        boolean with the value for noUpdate argument.
        The value should be False if the response was modified.
    """
    if response.status_code == 304:
        demisto.debug("No new indicators fetched, createIndicators will be executed with noUpdate=True.")
        return True

    etag = response.headers.get("ETag")
    if etag:
        etag = etag.strip('"')
    last_modified = response.headers.get("Last-Modified")
    current_time = datetime.utcnow()
    # Save the current time as the last updated time. This will be used to indicate the last time the feed was updated in XSOAR.
    last_updated = current_time.strftime(DATE_FORMAT)

    if not etag and not last_modified:
        demisto.debug("Last-Modified and Etag headers are not exists,createIndicators will be executed with noUpdate=False.")
        return False

    last_run = demisto.getLastRun()
    last_run[url] = {"last_modified": last_modified, "etag": etag, "last_updated": last_updated}
    demisto.setLastRun(last_run)

    demisto.debug(
        "New indicators fetched - the Last-Modified value has been updated,"
        " createIndicators will be executed with noUpdate=False."
    )
    return False


def datestring_to_server_format(date_string: str) -> str:
    """
    formats a datestring to the ISO-8601 format which the server expects to recieve
    :param date_string: Date represented as a tring
    :return: ISO-8601 date string
    """
    parsed_date = dateparser.parse(date_string, settings={"TIMEZONE": "UTC"})
    return parsed_date.strftime(DATE_FORMAT)  # type: ignore


def is_cidr_32(value: str) -> bool:
    """
    Checks if the given CIDR address is a /32.

    Parameters:
        value (str): A CIDR address.

    Returns:
        bool: True if the CIDR is /32, False otherwise.
    """
    try:
        return str(value).strip().endswith("/32")
    except ValueError:
        return False


def convert_cidr32_to_ip(value: str) -> Optional[str]:
    """
    Converts a CIDR /32 address to its IP part.

    Parameters:
        value (str): A CIDR address with /32 in the end

    Returns:
        Optional[str]: an ip address without the /32 string
    """
    ip, subnet = value.strip().split("/")
    return ip if subnet == "32" else None


def ip_range_to_cidr(start_ip: str, end_ip: str) -> list:
    """
    Converts an IP range into a CIDR.

    Args:
        start_ip (str): The first IP.
        end_ip (str): The last IP.

    Returns:
        list: List of CIDRs.
    """
    try:
        start = ip_address(start_ip)
        end = ip_address(end_ip)
        # Ensure both IPs are of the same type (IPv4 or IPv6)
        if start.version != end.version:
            raise ValueError("Start and End IP must be of the same type (IPv4 or IPv6).")
        # Summarize the IP range into CIDRs
        cidr_list = [str(cidr) for cidr in summarize_address_range(start, end)]
    except Exception as e:
        demisto.error(f'Could not convert IP range "{start_ip}-{end_ip}" to CIDR\n{e}')
        return []
    return cidr_list


def get_indicator_fields(line, url, feed_tags: list, tlp_color: Optional[str], client: Client):
    """
    Extract indicators according to the feed type
    :param line: The current line in the feed
    :param url: The feed URL
    :param client: The client
    :param feed_tags: The indicator tags.
    :param tlp_color: Traffic Light Protocol color.
    :return: Indicator list
    """
    attributes = None
    indicator = None
    extracted_indicator = None
    fields_to_extract = []
    feed_config = client.feed_url_to_config.get(url, {})
    if feed_config and "indicator" in feed_config:
        indicator = feed_config["indicator"]
        if "regex" in indicator:
            indicator["regex"] = re.compile(indicator["regex"])
        if "transform" not in indicator:
            indicator["transform"] = r"\g<0>"

    if "fields" in feed_config:
        fields = feed_config["fields"]
        for field in fields:
            for f, fattrs in field.items():
                field = {f: {}}
                if "regex" in fattrs:
                    field[f]["regex"] = re.compile(fattrs["regex"])
                field[f]["transform"] = fattrs.get("transform", "\\g<0>")
                fields_to_extract.append(field)

    line = line.strip()
    if line:
        extracted_indicator = line.split()[0]
        if indicator:
            extracted_indicator = indicator["regex"].search(line)
            if extracted_indicator is None:
                return attributes, []
            if "transform" in indicator:
                extracted_indicator = extracted_indicator.expand(indicator["transform"])
            if ip_range_match := re.fullmatch(IP_RANGE_REGEX_PATTERN, extracted_indicator):
                ip_start, ip_end = ip_range_match.groups()
                if cidr_list := ip_range_to_cidr(ip_start, ip_end):
                    extracted_indicator = cidr_list

        if not isinstance(extracted_indicator, list):
            extracted_indicator = [extracted_indicator]

        attributes = {}
        for field in fields_to_extract:
            for f, fattrs in field.items():
                m = fattrs["regex"].search(line)

                if m is None:
                    continue

                attributes[f] = m.expand(fattrs["transform"])

                try:
                    i = int(attributes[f])
                except Exception:
                    pass
                else:
                    attributes[f] = i

        attributes["type"] = feed_config.get("indicator_type", client.indicator_type)
        attributes["tags"] = feed_tags

        if tlp_color:
            attributes["trafficlightprotocol"] = tlp_color
    else:
        extracted_indicator = []

    return attributes, extracted_indicator


def process_indicator_type(
    client: Client, value: str, url: str, itype: str, auto_detect: bool, cidr_32_to_ip: bool
) -> tuple[str, bool]:
    """
    Processes the indicator value and configuration parameters to determine the indicator type.

    Args:
        client (Client): The feed client object used.
        value (str): The raw indicator value.
        url (str): The source URL associated with the indicator.
        itype (str): The indicator type provided by the user.
        auto_detect (bool): Whether XSOAR should auto-detect the indicator type.
        cidr_32_to_ip (bool, optional): If True, duplicate /32 CIDR indicators as IP indicators.

    Returns:
        Tuple[str, str, bool]: The indicator value, its determined type, and a flag indicating if it's a /32 CIDR.
    """
    indicator_type = determine_indicator_type(
        client.feed_url_to_config.get(url, {}).get("indicator_type"), itype, auto_detect, value
    )

    is_32_cidr = cidr_32_to_ip and indicator_type == FeedIndicatorType.CIDR and is_cidr_32(value)

    return indicator_type, is_32_cidr


def process_indicator_data(
    client, value, attributes, url, indicator_type, create_relationships=False, enrichment_excluded: bool = False
) -> dict:
    """Builds the indicator data object.

    Args:
        client: The client object.
        value: The indicator value.
        attributes: The indicator attributes.
        url: The URL.
        indicator_type: The indicator type.
        create_relationships: Whether to create relationsheeps.
        enrichment_excluded: Whether to enrich excluded..

    Returns:
         dict: The indicator data object.
    """
    attributes = attributes if attributes else {}
    attributes["value"] = value
    if "lastseenbysource" in attributes:
        attributes["lastseenbysource"] = datestring_to_server_format(attributes["lastseenbysource"])

    if "firstseenbysource" in attributes:
        attributes["firstseenbysource"] = datestring_to_server_format(attributes["firstseenbysource"])
        # if user asked that CIDR indicators will also appear as IP indicators.
    indicator_data = {
        "value": value,
        "type": indicator_type,
        "rawJSON": attributes.copy(),
    }
    if enrichment_excluded:
        indicator_data["enrichmentExcluded"] = enrichment_excluded

    if (
        create_relationships
        and client.feed_url_to_config.get(url, {}).get("relationship_name")
        and attributes.get("relationship_entity_b")
    ):
        relationships_lst = EntityRelationship(
            name=client.feed_url_to_config.get(url, {}).get("relationship_name"),
            entity_a=value,
            entity_a_type=indicator_type,
            entity_b=attributes.get("relationship_entity_b"),
            entity_b_type=FeedIndicatorType.indicator_type_by_server_version(
                client.feed_url_to_config.get(url, {}).get("relationship_entity_b_type")
            ),
        )
        relationships_of_indicator = [relationships_lst.to_indicator()]
        indicator_data["relationships"] = relationships_of_indicator

    if len(client.custom_fields_mapping.keys()) > 0 or TAGS in attributes:
        custom_fields = client.custom_fields_creator(attributes)
        indicator_data["fields"] = custom_fields

    return indicator_data


def determine_indicator_type(indicator_type, default_indicator_type, auto_detect, value):
    """
    Detect the indicator type of the given value.
    Args:
        indicator_type: (str) Indicator type given in the config.
        default_indicator_type: Indicator type which was inserted as a param of the integration by user.
        auto_detect: (bool) True whether auto detection of the indicator type is wanted.
        value: (str) The value which we'd like to get indicator type of.
    Returns:
        Str which stands for the indicator type after detection.
    """
    if auto_detect:
        indicator_type = auto_detect_indicator_type(value)
    if not indicator_type:
        indicator_type = default_indicator_type
    return indicator_type


def get_indicators_command(client: Client, args, enrichment_excluded: bool = False):
    itype = args.get("indicator_type", client.indicator_type)
    limit = int(args.get("limit"))
    feed_tags = args.get("feedTags")
    tlp_color = args.get("tlp_color")
    cidr_32_to_ip = args.get("cidr_32_to_ip")
    auto_detect = demisto.params().get("auto_detect_type")
    create_relationships = demisto.params().get("create_relationships")
    indicators_list, _ = fetch_indicators_command(
        client, feed_tags, tlp_color, itype, auto_detect, create_relationships, cidr_32_to_ip, enrichment_excluded
    )[:limit]
    entry_result = camelize(indicators_list)
    hr = tableToMarkdown("Indicators", entry_result, headers=["Value", "Type", "Rawjson"])
    return hr, {}, indicators_list


def fetch_indicators_command(
    client,
    feed_tags,
    tlp_color,
    itype,
    auto_detect,
    create_relationships=False,
    cidr_32_to_ip=False,
    enrichment_excluded: bool = False,
    **kwargs,
):
    iterators = client.build_iterator(**kwargs)
    indicators = []

    # set noUpdate flag in createIndicators command True only when all the results from all the urls are True.
    no_update = all(next(iter(iterator.values())).get("no_update", False) for iterator in iterators)

    for iterator in iterators:
        for url, lines in iterator.items():
            for line in lines.get("result", []):
                attributes, indicator_values = get_indicator_fields(line, url, feed_tags, tlp_color, client)
                demisto.debug(f"Got the following indicator values - {indicator_values}")

                for indicator_value in indicator_values:
                    indicator_type, is_32_cidr = process_indicator_type(
                        client=client,
                        value=indicator_value,
                        url=url,
                        itype=itype,
                        auto_detect=auto_detect,
                        cidr_32_to_ip=cidr_32_to_ip,
                    )
                    indicators.append(
                        process_indicator_data(
                            client, indicator_value, attributes, url, indicator_type, create_relationships, enrichment_excluded
                        )
                    )

                    if is_32_cidr:
                        ip_value = convert_cidr32_to_ip(indicator_value)
                        attributes["type"] = FeedIndicatorType.IP
                        indicators.append(
                            process_indicator_data(
                                client, ip_value, attributes, url, FeedIndicatorType.IP, create_relationships, enrichment_excluded
                            )
                        )

    return indicators, no_update


def test_module(client: Client, args):
    if not client.feed_url_to_config:
        indicator_type = args.get("indicator_type", demisto.params().get("indicator_type"))
        if not FeedIndicatorType.is_valid_type(indicator_type):
            indicator_types = []
            for key, val in vars(FeedIndicatorType).items():
                if not key.startswith("__") and isinstance(val, str):
                    indicator_types.append(val)
            supported_values = ", ".join(indicator_types)
            raise ValueError(f"Indicator type of {indicator_type} is not supported. Supported values are: {supported_values}")
    client.build_iterator()
    return "ok", {}, {}


def feed_main(feed_name, params=None, prefix=""):
    if not params:
        params = assign_params(**demisto.params())
    if "feed_name" not in params:
        params["feed_name"] = feed_name
    feed_tags = argToList(demisto.params().get("feedTags"))
    tlp_color = demisto.params().get("tlp_color")
    cidr_32_to_ip = argToBoolean(demisto.params().get("cidr_32_to_ip", False))
    enrichment_excluded = demisto.params().get("enrichmentExcluded", False) or (
        demisto.params().get("tlp_color") == "RED" and is_xsiam_or_xsoar_saas()
    )
    client = Client(**params)
    command = demisto.command()
    if command != "fetch-indicators":
        demisto.info("Command being called is {}".format(command))
    if prefix and not prefix.endswith("-"):
        prefix += "-"
    # Switch case
    commands: dict = {"test-module": test_module, f"{prefix}get-indicators": get_indicators_command}
    try:
        if command == "fetch-indicators":
            indicators, no_update = fetch_indicators_command(
                client,
                feed_tags,
                tlp_color,
                params.get("indicator_type"),
                params.get("auto_detect_type"),
                params.get("create_relationships"),
                cidr_32_to_ip,
                enrichment_excluded=enrichment_excluded,
            )

            # check if the version is higher than 6.5.0 so we can use noUpdate parameter
            if is_demisto_version_ge("6.5.0"):
                if not indicators:
                    demisto.createIndicators(indicators, noUpdate=no_update)  # type: ignore
                else:
                    # we submit the indicators in batches
                    for b in batch(indicators, batch_size=2000):
                        demisto.createIndicators(b, noUpdate=no_update)  # type: ignore
            else:
                # call createIndicators without noUpdate arg
                if not indicators:
                    demisto.createIndicators(indicators)  # type: ignore
                else:
                    for b in batch(indicators, batch_size=2000):  # type: ignore
                        demisto.createIndicators(b)

        else:
            args = demisto.args()
            args["feed_name"] = feed_name
            if feed_tags:
                args["feedTags"] = feed_tags
            if tlp_color:
                args["tlp_color"] = tlp_color
            args["cidr_32_to_ip"] = cidr_32_to_ip
            readable_output, outputs, raw_response = commands[command](client, args)
            return_outputs(readable_output, outputs, raw_response)
    except Exception as e:
        err_msg = f"Error in {feed_name} integration [{e}]"
        return_error(err_msg, error=e)
