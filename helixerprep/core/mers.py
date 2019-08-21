import os
from glob import glob
from shutil import copyfile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import geenuff
from geenuff.base.helpers import full_db_path, reverse_complement
from geenuff.base.orm import Coordinate, Genome
from helixerprep.core.orm import Mer


class MerController(object):
    def __init__(self, db_path_in, db_path_out, meta_info_root_path):
        self.meta_info_root_path = meta_info_root_path
        self._setup_db(db_path_in, db_path_out)
        self._mk_session()

    def _setup_db(self, db_path_in, db_path_out):
        self.db_path = db_path_out
        if db_path_out != '':
            if os.path.exists(db_path_out):
                print('overriding the helixer output db at {}'.format(db_path_out))
            copyfile(db_path_in, db_path_out)
        else:
            print('adding the helixer additions directly to input db at {}'.format(db_path_in))
            self.db_path = db_path_in

    def _mk_session(self):
        self.engine = create_engine(full_db_path(self.db_path), echo=False)
        # add Helixer specific table to the input db if it doesn't exist yet
        if not self.engine.dialect.has_table(self.engine, 'mer'):
            geenuff.orm.Base.metadata.tables['mer'].create(self.engine)
        self.session = sessionmaker(bind=self.engine)()

    def _add_mers_of_seqid(self, species, seqid, mers):
        print(species, seqid)
        genome_id = self.session.query(Genome.id).filter(Genome.species == species).one()[0]
        coord_id = (self.session.query(Coordinate.id)
                       .filter(Coordinate.genome_id == genome_id)
                       .filter(Coordinate.seqid == seqid)
                       .one())[0]
        for mer_sequence, count in mers.items():
            mer = Mer(coordinate_id=coord_id,
                      mer_sequence=mer_sequence,
                      count=count,
                      length=len(mer_sequence))
            self.session.add(mer)
        self.session.commit()

    def add_mer_counts_to_db(self):
        """Tries to add all kmer counts it can find for each coordinate in the db
        Assumes the kmer file to contain non-collapsed kmers ordered by coordinate first and kmer
        sequence second"""
        assert os.path.exists(self.meta_info_root_path)
        genomes_in_db = self.session.query(Genome).all()
        for i, genome in enumerate(genomes_in_db):
            kmer_file = os.path.join(self.meta_info_root_path, genomes_in_db[i].species,
                                     'meta_collection', 'kmers', 'kmers.tsv')
            if os.path.exists(kmer_file):
                last_seqid = ''
                seqid_mers = {}  # here we collect the sum of the
                for i, line in enumerate(open(kmer_file)):
                    # loop setup
                    if i == 0:
                        continue  # skip header
                    seqid, mer_sequence, count, _ = line.strip().split('\t')
                    count = int(count)
                    if i == 1:
                        last_seqid = seqid

                    # insert coordinate mers
                    if last_seqid != seqid:
                        self._add_mers_of_seqid(genome.species, last_seqid, seqid_mers)
                        seqid_mers = {}
                        last_seqid = seqid

                    # figure out kmer collapse
                    rc = ''.join(reverse_complement(mer_sequence))
                    key = rc if rc < mer_sequence else mer_sequence
                    if key in seqid_mers:
                        seqid_mers[key] += count
                    else:
                        seqid_mers[key] = count
                self._add_mers_of_seqid(genome.species, last_seqid, seqid_mers)
                print('Kmers from file {} added\n'.format(kmer_file))
