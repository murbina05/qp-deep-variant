# -----------------------------------------------------------------------------
# Copyright (c) 2020--, The Qiita Development Team.
#
# Distributed under the terms of the BSD 3-clause License.
#
# The full license is in the file LICENSE, distributed with this software.
# -----------------------------------------------------------------------------
from unittest import main
from qiita_client.testing import PluginTestCase
from os import remove, environ
from os.path import exists, isdir, join, dirname, basename
from shutil import rmtree, copyfile
from tempfile import mkdtemp
from json import dumps
from functools import partial
from qp_ivar_trim import plugin
from qp_ivar_trim.utils import plugin_details
from qp_ivar_trim.qp_ivar_trim import (
    get_primer, _generate_commands, ivar_trim_to_array, QC_REFERENCE,
    IVAR_TRIM_CMD)


class IvarTrimTests(PluginTestCase):
    def setUp(self):
        plugin("https://localhost:21174", 'register', 'ignored')

        out_dir = mkdtemp()
        self.maxDiff = None
        self.out_dir = out_dir
        self.primer = get_primer()
        self.primer_path = QC_REFERENCE
        self.params = {'primer': 'primer', 'primer_offset': 5}
        self._clean_up_files = []
        self._clean_up_files.append(out_dir)

    def tearDown(self):
        for fp in self._clean_up_files:
            if exists(fp):
                if isdir(fp):
                    rmtree(fp)
                else:
                    remove(fp)

    def test_get_primer(self):
        primer = get_primer()
        self.assertEqual(primer, 'primer.bed')

    def test_generate_commands(self):
        params = {'primer_offset': 5,
                  'primer': 'primer',
                  'out_dir': '/foo/bar/output'}
        # need to change these to bam
        bam_file = ['CALM_SEP_001970_03_S265_L001.sorted.bam.gz',
                    'CALM_SEP_001970_03_S265_L002.sorted.bam.gz']
        obs = _generate_commands(bam_file,
                                 params['primer'],
                                 params['primer_offset'],
                                 params['out_dir'])
        cmd = IVAR_TRIM_CMD.format(
            bam_file, primer_offset=params['primer_offset'],
            primer=params['primer'], out_dir=params['out_dir'])
        ecmds = []
        for bam_gz in bam_file:
            fname_gz = basename(bam_gz)
            fname = fname_gz[:-3]
            bam = bam_gz[:-3]
            ecmds.append(cmd % (bam_gz, bam, fname, fname))
        eof = [(f'{params["out_dir"]}/{bam}', 'tgz')
               for bam in bam_file]
        self.assertCountEqual(obs[0], ecmds)
        self.assertCountEqual(obs[1], eof)

    def test_ivar_trim(self):
        # inserting new prep template
        prep_info_dict = {
            'SKB8.640193': {'run_prefix': 'CALM_SEP_001970_03'}}
        data = {'prep_info': dumps(prep_info_dict),
                # magic #1 = testing study
                'study': 1,
                'data_type': 'Metagenomic'}
        pid = self.qclient.post('/apitest/prep_template/', data=data)['prep']

        fname_1 = 'CALM_SEP_001970_03_S265_L001'
        fname_2 = 'CALM_SEP_001970_03_S265_L002'
        # inserting artifacts
        in_dir = mkdtemp()
        self._clean_up_files.append(in_dir)

        fp1_1 = join(in_dir, 'CALM_SEP_001970_03_S265_L001.sorted.bam.gz')
        fp1_2 = join(in_dir, 'CALM_SEP_001970_03_S265_L002.sorted.bam.gz')
        # fp2_1 = join(in_dir, 'S22282_S102_L001_R1_001.fastq.gz')
        # fp2_2 = join(in_dir, 'S22282_S102_L001_R2_001.fastq.gz')
        source_dir = 'qp_ivar_trim/support_files/raw_data'
        copyfile(f'{source_dir}/{fname_1}.sorted.bam.gz',
                 fp1_1)
        copyfile(f'{source_dir}/{fname_2}.sorted.bam.gz',
                 fp1_2)

        data = {
            'filepaths': dumps([
                (fp1_1, 'tgz'),
                (fp1_2, 'tgz')]),
            'type': "BAM",
            'name': "Test artifact",
            'prep': pid}
        aid = self.qclient.post('/apitest/artifact/', data=data)['artifact']

        self.params['input'] = aid

        data = {'user': 'demo@microbio.me',
                'command': dumps([plugin_details['name'],
                                  plugin_details['version'],
                                  'ivar trim']),
                'status': 'running',
                'parameters': dumps(self.params)}
        job_id = self.qclient.post(
            '/apitest/processing_job/', data=data)['job']

        out_dir = mkdtemp()
        self._clean_up_files.append(out_dir)

        # adding extra parameters
        self.params['environment'] = environ["ENVIRONMENT"]

        # Get the artifact filepath information
        artifact_info = self.qclient.get("/qiita_db/artifacts/%s/" % aid)

        # Get the artifact metadata
        prep_info = self.qclient.get('/qiita_db/prep_template/%s/' % pid)
        prep_file = prep_info['prep-file']

        url = 'this-is-my-url'

        main_qsub_fp, finish_qsub_fp, out_files_fp = ivar_trim_to_array(
            artifact_info['files'], out_dir, self.params, prep_file,
            url, job_id)

        od = partial(join, out_dir)
        self.assertEqual(od(f'{job_id}.qsub'), main_qsub_fp)
        self.assertEqual(od(f'{job_id}.finish.qsub'), finish_qsub_fp)
        self.assertEqual(od(f'{job_id}.out_files.tsv'), out_files_fp)

        with open(main_qsub_fp) as f:
            main_qsub = f.readlines()
        with open(finish_qsub_fp) as f:
            finish_qsub = f.readlines()
        with open(out_files_fp) as f:
            out_files = f.readlines()
        with open(f'{out_dir}/ivar_trim.array-details') as f:
            commands = f.readlines()

        exp_main_qsub = [
            '#!/bin/bash\n',
            '#PBS -M qiita.help@gmail.com\n',
            f'#PBS -N {job_id}\n',
            '#PBS -l nodes=1\n',
            '#PBS -l walltime=30:00:00\n',
            '#PBS -l mem=16g\n',
            f'#PBS -o {out_dir}/{job_id}_${{PBS_ARRAYID}}.log\n',
            f'#PBS -e {out_dir}/{job_id}_${{PBS_ARRAYID}}.err\n',
            '#PBS -t 1-2%8\n',
            '#PBS -l epilogue=/home/qiita/qiita-epilogue.sh\n',
            'set -e\n',
            f'cd {out_dir}\n',
            'source /home/runner/.profile; source activate qp-ivar-trim; '
            f'export QC_REFERENCE={QC_REFERENCE}\n',
            'date\n',
            'hostname\n',
            'echo ${PBS_JOBID} ${PBS_ARRAYID}\n',
            'offset=${PBS_ARRAYID}\n', 'step=$(( $offset - 0 ))\n',
            f'cmd=$(head -n $step {out_dir}/ivar_trim.array-details | '
            'tail -n 1)\n',
            'eval $cmd\n',
            'set +e\n',
            'date\n']
        self.assertEqual(main_qsub, exp_main_qsub)

        exp_finish_qsub = [
            '#!/bin/bash\n',
            '#PBS -M qiita.help@gmail.com\n',
            f'#PBS -N finish-{job_id}\n',
            '#PBS -l nodes=1\n',
            '#PBS -l walltime=10:00:00\n',
            '#PBS -l mem=10g\n',
            f'#PBS -o {out_dir}/finish-{job_id}.log\n',
            f'#PBS -e {out_dir}/finish-{job_id}.err\n',
            '#PBS -l epilogue=/home/qiita/qiita-epilogue.sh\n',
            'set -e\n',
            f'cd {out_dir}\n',
            'source /home/runner/.profile; source activate qp-ivar-trim; '
            f'export QC_REFERENCE={QC_REFERENCE}\n',
            'date\n',
            'hostname\n',
            'echo $PBS_JOBID\n',
            f'finish_qp_ivar_trim this-is-my-url {job_id} {out_dir}\n',
            'date\n']
        self.assertEqual(finish_qsub, exp_finish_qsub)

        exp_out_files = [
            f'{out_dir}/{fname_1}.sorted.bam.gz\ttgz\n',
            f'{out_dir}/{fname_2}.sorted.bam.gz\ttgz']
        self.assertEqual(out_files, exp_out_files)

        # the easiest to figure out the location of the artifact input files
        # is to check the first file of the raw forward reads
        apath = dirname(artifact_info['files']['tgz'][0])
        # exp_commands = [
        #    f'fastp -l 100 -i {apath}/S22205_S104_L001_R1_001.fastq.gz -w 2  '
        #    f'-I {apath}/S22205_S104_L001_R2_001.fastq.gz --stdout | '
        #    f'{out_dir}/S22205_S104_L001_R1_001.fastq.gz -2 '
        #    f'{out_dir}/S22205_S104_L001_R2_001.fastq.gz\n',
        #    f'fastp -l 100 -i {apath}/S22282_S102_L001_R1_001.fastq.gz -w 2  '
        #    f'-I {apath}/S22282_S102_L001_R2_001.fastq.gz --stdout | '
        #    f'{out_dir}/S22282_S102_L001_R1_001.fastq.gz -2 '
        #    f'{out_dir}/S22282_S102_L001_R2_001.fastq.gz']
        exp_commands = [
            f'gunzip {apath}/{fname_1}.sorted.bam.gz; '
            f'ivar trim -x 5 -e -i {apath}/{fname_1}.sorted.bam '
            f'-b {QC_REFERENCE}primer.bed '
            f'-p {out_dir}/{fname_1}.sorted.bam; '
            f'gzip {out_dir}/{fname_1}.sorted.bam\n',
            f'gunzip {apath}/{fname_2}.sorted.bam.gz; '
            f'ivar trim -x 5 -e -i {apath}/{fname_2}.sorted.bam '
            f'-b {QC_REFERENCE}primer.bed '
            f'-p {out_dir}/{fname_2}.sorted.bam; '
            f'gzip {out_dir}/{fname_2}.sorted.bam'
        ]
        self.assertEqual(commands, exp_commands)


if __name__ == '__main__':
    main()
