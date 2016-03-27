#!/usr/bin/env python

#
# LSST Data Management System
# Copyright 2014-2015 LSST/AURA.
#
# This product includes software developed by the
# LSST Project (http://www.lsst.org/).
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the LSST License Statement and
# the GNU General Public License along with this program.  If not,
# see <http://www.lsstcorp.org/LegalNotices/>.
#


__author__ = 'bvan'

"""
A Simple single-threaded crawler-like application.
This crawler only scans one folder at a time, retrieving up to 1000 results at
a time.
It searches for datasets which are unscanned for a particular location.
Added code to write FITS header and file information to foreign tables and
supply DataCat metadata with the foreign table fileId.
"""

import sys
import os
import sched
import time
import subprocess
from datacat import Client, unpack
from datacat.config import CONFIG_URL
from datacat.client import DcException
from datetime import datetime

import lsst.log as log
from lsst.db.engineFactory import getEngineFromFile
from .MetadataFitsDb import MetadataFitsDb
from .dataCatUtil import DataCatCfg

WATCH_FOLDER = '/LSST'
WATCH_SITE = 'SLAC'


class Crawler:

    RERUN_SECONDS = 5

    def __init__(self, dataCatCfg):
        self._dataCatCfg = dataCatCfg
        self.client = Client(self._dataCatCfg.getRestUrl())
        self.sched = sched.scheduler(time.time, time.sleep)
        self._run()

    def start(self):
        self.sched.run()

    def _run(self):
        self.run()
        self.sched.enter(Crawler.RERUN_SECONDS, 1, self._run, ())

    def get_cksum(self, path):
        cksum_proc = subprocess.Popen(["cksum", path], stdout=subprocess.PIPE)
        ec = cksum_proc.wait()
        if ec != 0:
            # Handle error here, or raise exception/error
            pass
        cksum_out = cksum_proc.stdout.read().split(" ")
        cksum = cksum_out[0]
        return cksum

    def get_metadata(self, path):
        return None

    def run(self):
        credFileName = "~/.lsst/dbAuth-dbServ.ini"
        engine = getEngineFromFile(credFileName)
        dbName = "{}_fitsTest".format(engine.url.username)
        metaDb = MetadataFitsDb(credFileName)

        resp = None
        try:
            resp = self.client.search(WATCH_FOLDER, version="current", site="all",
                                      query="scanStatus = 'UNSCANNED'", max_num=1000)
        except DcException as error:
            if hasattr(error, "message"):
                log.warn("Error occurred:\nMessage: %s", error.message)
                if hasattr(error, "type"):
                    log.warn("Type: %s", error.type)
                if hasattr(error, "cause"):
                    log.warn("Cause: %s", error.cause)
            else:
                # Should have content
                log.warn(error.content)
            sys.exit(1)

        results = unpack(resp.content)

        for dataset in results:
            locations = dataset.locations
            check_location = None
            for location in locations:
                if location.site == WATCH_SITE:
                    check_location = location
                    break
            file_path = check_location.resource
            dataset_path = dataset.path
            stat = os.stat(file_path)
            cksum = self.get_cksum(file_path)

            # Note: While there may only be one version of a dataset,
            # we tie the metadata to versionMetadata
            scan_result = {}
            scan_result["size"] = stat.st_size
            scan_result["checksum"] = str(cksum)
            # UTC datetime in ISO format (Note: We need Z to denote UTC Time Zone)
            scan_result["locationScanned"] = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
            scan_result["scanStatus"] = "OK"

            md = self.get_metadata(file_path)
            if md:
                scan_result["versionMetadata"] = md

            try:
                log.debug("patch_resp %s", str(file_path))
                patch_resp = self.client.patch_dataset(dataset_path, scan_result,
                                                       versionId=dataset.versionId, site=WATCH_SITE)
                log.debug("Inserting %s", str(file_path))
                fileId = metaDb.insertFile(file_path)
                metadata = {"fileId": fileId}
                md_patch = {}
                md_patch["versionMetadata"] = metadata
                md_patch_resp = self.client.patch_dataset(dataset_path, md_patch,
                                                          versionId=dataset.versionId)
                log.info("Inserted %d %s", fileId, str(file_path))
            except DcException as err:
                log.warn("Encountered error while updating dataset %s", str(file_path), err)


def main():
    log.setLevel("", log.DEBUG)
    dataCatCfg = DataCatCfg()
    c = Crawler(dataCatCfg)
    c.start()

if __name__ == '__main__':
    main()
