#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import sys
import logging
import argparse

from tqdm import tqdm
from peewee import PeeweeException, OperationalError

from taxadb import util
from taxadb import download
from taxadb.version import __version__
from taxadb.schema import DatabaseFactory, db, Taxa, Accession
from taxadb.parser import TaxaDumpParser, Accession2TaxidParser


def download_files(args):
    """Main function for the `taxadb download` sub-command.

    This function can download taxump.tar.gz and the content of the
    accession2taxid directory from the ncbi ftp.

    Arguments:
             args (object): The arguments from argparse

    """
    logger = logging.getLogger(__name__)

    # files to download
    # nucl_est = 'nucl_est.accession2taxid.gz'  # deprecated
    nucl_gb = 'nucl_gb.accession2taxid.gz'
    # nucl_gss = 'nucl_gss.accession2taxid.gz'  # deprecated
    nucl_wgs = 'nucl_wgs.accession2taxid.gz'
    prot = 'prot.accession2taxid.gz'
    taxdump = 'taxdump.tar.gz'

    args.type = [x for y in args.type for x in y]
    acc_dl_list = [taxdump]

    for div in args.type:
        if div in ['full', 'nucl', 'gb']:
            acc_dl_list.append(nucl_gb)
        if div in ['full', 'nucl', 'wgs']:
            acc_dl_list.append(nucl_wgs)
        if div in ['full', 'prot']:
            acc_dl_list.append(prot)

    try:
        out = args.outdir
        os.makedirs(os.path.abspath(out), exist_ok=args.force)
        os.chdir(os.path.abspath(out))
    except FileExistsError as e:
        logger.error('%s exists. Consider using -f if you want to overwrite'
                     % out)
        sys.exit(1)

    for file in acc_dl_list:
        if file != taxdump:
            download.ncbi('pub/taxonomy/accession2taxid/', file)
            download.ncbi('pub/taxonomy/accession2taxid/', file + '.md5')
            util.md5_check(file)
        else:
            download.ncbi('pub/taxonomy/', taxdump)
            download.ncbi('pub/taxonomy/', taxdump + '.md5')
            util.md5_check(taxdump)
            download.unpack(taxdump)


def create_db(args):
    """Main function for the 'taxadb create' sub-command.

    This function creates a taxonomy database with 2 tables: Taxa and Sequence.

    Args:

        args.input (:obj:`str`): input directory. It is the directory created
            by `taxadb download`
        args.dbname (:obj:`str`): name of the database to be created
        args.dbtype (:obj:`str`): type of database to be used.
        args.division (:obj:`str`): division to create the db for.
        args.fast (:obj:`bool`): Disables checks for faster db creation. Use
                                 with caution!

    """
    logger = logging.getLogger(__name__)
    database = DatabaseFactory(**args.__dict__).get_database()
    div = args.division  # am lazy at typing
    db.initialize(database)

    nucl_gb = 'nucl_gb.accession2taxid.gz'
    nucl_wgs = 'nucl_wgs.accession2taxid.gz'
    prot = 'prot.accession2taxid.gz'
    acc_dl_list = []

    db.connect()
    parser = TaxaDumpParser(nodes_file=os.path.join(args.input, 'nodes.dmp'),
                            names_file=os.path.join(args.input, 'names.dmp'),
                            verbose=args.verbose)

    logger.debug('Connected to database')
    # If taxa table already exists, do not recreate and fill it
    # safe=True prevent not to create the table if it already exists
    if not Taxa.table_exists():
        logger.info('Creating table %s' % str(Taxa.get_table_name()))
        db.create_tables([Taxa])

    logger.info("Parsing files")
    taxa_info_list = parser.taxdump()

    logger.info("Inserting taxonomy data")
    total_size = len(taxa_info_list)
    try:
        with db.atomic():
            for i in tqdm(range(0, total_size, args.chunk),
                          unit=' chunks', desc='INFO:taxadb.app',
                          total=''):
                Taxa.insert_many(taxa_info_list[i:i+args.chunk]).execute()
    except OperationalError as e:
        print("\n")  # needed because the above counter has none
        logger.error("sqlite3 error: %s" % e)
        logger.error("Maybe retry with a lower chunk size.")
        sys.exit(1)
    logger.info('Table Taxa completed')

    # At first load, table accession does not exist yet, we create it
    db.create_tables([Accession])

    if div in ['full', 'nucl', 'gb']:
        acc_dl_list.append(nucl_gb)
    if div in ['full', 'nucl', 'wgs']:
        acc_dl_list.append(nucl_wgs)
    if div in ['full', 'prot']:
        acc_dl_list.append(prot)
    parser = Accession2TaxidParser(verbose=args.verbose, fast=args.fast)
    with db.atomic() as txn:
        for acc_file in acc_dl_list:
            inserted_rows = 0
            logger.info("Parsing %s" % str(acc_file))
            for data_dict in tqdm(
                parser.accession2taxid(
                    acc2taxid=os.path.join(args.input, acc_file),
                    chunk=args.chunk), unit=' chunks',
                    desc='INFO:taxadb.app',
                    total=''):
                Accession.insert_many(data_dict[0:args.chunk]).execute()
                inserted_rows += len(data_dict)
            logger.info('%s: %s added to database (%d rows inserted)'
                        % (Accession.get_table_name(),
                            acc_file, inserted_rows))
            txn.commit()
        if not Accession.has_index(name='accession_accession'):
            logger.info('Creating index for %s'
                        % Accession.get_table_name())
            try:
                # db.add_index(Accession, ['accession'], unique=True)
                idx = db.index(db.Accession, name='accession', unique=True)
                db.add_index(idx)
                txn.commit()
            except PeeweeException as err:
                raise Exception("Could not create Accession index: %s"
                                % str(err))
    logger.info('Table Accession completed')
    db.close()


def query(args):
    print('This has not been implemented yet. Sorry :-(')


def main():
    parser = argparse.ArgumentParser(
        prog='taxadb',
        usage='taxadb <command> [options]',
        description='download and create the database used by the taxadb \
        library'
    )
    parser.add_argument(
        '-v',
        '--version',
        action='store_true',
        default=False,
        help='print software version and exit'
    )

    subparsers = parser.add_subparsers(
        title='available commands',
        metavar=''
    )

    parser_download = subparsers.add_parser(
        'download',
        prog='taxadb download',
        description='download the files required to create the database',
        help='download the files required to create the database'
    )
    param_logging_dl = parser_download.add_mutually_exclusive_group()
    param_logging_dl.add_argument(
        '--quiet',
        action='store_true',
        default=False,
        help='Disable info logging. (default: %(default)s).'
    )
    param_logging_dl.add_argument(
        '--verbose',
        action="store_true",
        default=False,
        help='Enable debug logging. (default: %(default)s).'
    )
    parser_download.add_argument(
        '--type',
        '-t',
        choices=['taxa', 'full', 'nucl', 'prot', 'gb', 'wgs'],
        action='append',
        nargs='*',
        metavar='<str>',
        required=True,
        help='divisions to download. Can be one or more of "taxa", "full",\
            "nucl", "prot", "gb", or "wgs". Space-separated.'
    )
    parser_download.add_argument(
        '--force',
        '-f',
        action="store_true",
        default=False,
        help='Force download if the output directory exists',
    )
    parser_download.add_argument(
        '--outdir',
        '-o',
        metavar='<dir>',
        help='Output Directory',
        required=True
    )
    parser_download.set_defaults(func=download_files)

    parser_create = subparsers.add_parser(
        'create',
        prog='taxadb create',
        description='build the database',
        help='build the database'
    )
    param_logging_cr = parser_create.add_mutually_exclusive_group()
    param_logging_cr.add_argument(
        '--quiet',
        action='store_true',
        default=False,
        help='Disable info logging. (default: %(default)s).'
    )
    param_logging_cr.add_argument(
        '--verbose',
        action="store_true",
        default=False,
        help='Enable debug logging. (default: %(default)s).'
    )
    parser_create.add_argument(
        '--fast',
        action='store_true',
        default=False,
        help='Disables checks for faster db creation. Use with caution!'
    )
    parser_create.add_argument(
        '--chunk',
        '-c',
        metavar='<#chunk>',
        type=int,
        help='Number of sequences to insert in bulk (default: %(default)s)',
        default=500
    )
    parser_create.add_argument(
        '--input',
        '-i',
        metavar='<dir>',
        help='Input directory (where you first downloaded the files)',
        required=True
    )
    parser_create.add_argument(
        '--dbname',
        '-n',
        default='taxadb',
        metavar='taxadb',
        help='name of the database (default: %(default)s))'
    )
    parser_create.add_argument(
        '--dbtype',
        '-t',
        choices=['sqlite', 'mysql', 'postgres'],
        default='sqlite',
        metavar='[sqlite|mysql|postgres]',
        help='type of the database (default: %(default)s))'
    )
    parser_create.add_argument(
        '--division',
        '-d',
        choices=['taxa', 'full', 'nucl', 'prot', 'gb', 'wgs'],
        default='full',
        metavar='[taxa|full|nucl|prot|gb|wgs]',
        help='division to build (default: %(default)s))'
    )
    parser_create.add_argument(
        '--hostname',
        '-H',
        default='localhost',
        action="store",
        help='Database connection host (Optional, for MySQLdatabase and \
            PostgreSQLdatabase) (default: %(default)s)'
    )
    parser_create.add_argument(
        '--password',
        '-p',
        default=None,
        help='Password to use (required for MySQLdatabase \
            and PostgreSQLdatabase)'
    )
    parser_create.add_argument(
        '--port',
        '-P',
        type=int,
        help='Database connection port (default: 5432 (postgres), \
            3306 (MySQL))'
    )
    parser_create.add_argument(
        '--username',
        '-u',
        default=None,
        help='Username to login as (required for MySQLdatabase \
            and PostgreSQLdatabase)'
    )
    parser_create.set_defaults(func=create_db)

    parser_query = subparsers.add_parser(
        'query',
        prog='taxadb query',
        description='taxadb query is not implemented yet. Come back later!',
        help='query the database'
    )
    parser_query.set_defaults(func=query)

    args = parser.parse_args()
    try:
        if args.version:
            print('taxadb version %s' % __version__)
            sys.exit(0)
        elif args.quiet:
            logging.basicConfig(level=logging.ERROR)
        elif args.verbose:
            logging.basicConfig(level=logging.DEBUG)
        else:
            logging.basicConfig(level=logging.INFO)

        args.func(args)
        logging.shutdown()
    except AttributeError as e:
        logger = logging.getLogger(__name__)
        logger.debug(e)
        parser.print_help()
        # raise  # extra traceback to uncomment for extra debugging powers
