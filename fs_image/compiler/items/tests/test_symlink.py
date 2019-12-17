#!/usr/bin/env python3
import shlex
import sys
import tempfile

from compiler.provides import ProvidesDirectory, ProvidesFile
from compiler.requires import require_directory, require_file
from tests.temp_subvolumes import TempSubvolumes

from ..install_file import InstallFileItem
from ..symlink import SymlinkToDirItem, SymlinkToFileItem

from .common import BaseItemTestCase, DUMMY_LAYER_OPTS, render_subvol


class SymlinkItemsTestCase(BaseItemTestCase):

    def test_symlink(self):
        self._check_item(
            SymlinkToDirItem(from_target='t', source='x', dest='y'),
            {ProvidesDirectory(path='y')},
            {require_directory('/'), require_directory('/x')},
        )

        self._check_item(
            SymlinkToFileItem(
                from_target='t', source='source_file', dest='dest_symlink'
            ),
            {ProvidesFile(path='dest_symlink')},
            {require_directory('/'), require_file('/source_file')},
        )

    def test_symlink_command(self):
        with TempSubvolumes(sys.argv[0]) as temp_subvolumes:
            subvol = temp_subvolumes.create('tar-sv')
            subvol.run_as_root(['mkdir', subvol.path('dir')])

            # We need a source file to validate a SymlinkToFileItem
            with tempfile.NamedTemporaryFile() as tf:
                InstallFileItem(
                    from_target='t', source=tf.name, dest='/file',
                ).build(subvol, DUMMY_LAYER_OPTS)

            SymlinkToDirItem(
                from_target='t', source='/dir', dest='/dir_symlink'
            ).build(subvol, DUMMY_LAYER_OPTS)
            SymlinkToFileItem(
                from_target='t', source='file', dest='/file_symlink'
            ).build(subvol, DUMMY_LAYER_OPTS)

            def quoted_subvol_path(p):
                return shlex.quote(subvol.path(p).decode())

            # Make a couple of absolute symlinks to test our behavior on
            # linking to paths that contain those.
            subvol.run_as_root(['bash', '-c', f'''\
                ln -s /file {quoted_subvol_path('abs_link_to_file')}
                mkdir {quoted_subvol_path('my_dir')}
                touch {quoted_subvol_path('my_dir/inner')}
                ln -s /my_dir {quoted_subvol_path('my_dir_link')}
            '''])
            # A simple case: we link to an absolute link.
            SymlinkToFileItem(
                from_target='t',
                source='/abs_link_to_file',
                dest='/link_to_abs_link',
            ).build(subvol, DUMMY_LAYER_OPTS)
            # This link traverses a directory that is an absolute link.  The
            # resulting relative symlink is not traversible from outside the
            # container.
            SymlinkToFileItem(
                from_target='t',
                source='my_dir_link/inner',
                dest='/dir/inner_link',
            ).build(subvol, DUMMY_LAYER_OPTS)

            self.assertEqual(['(Dir)', {
                'dir': ['(Dir)', {
                    'inner_link': ['(Symlink ../my_dir_link/inner)'],
                }],
                'dir_symlink': ['(Symlink dir)'],
                'file': ['(File m444)'],
                'file_symlink': ['(Symlink file)'],

                'abs_link_to_file': ['(Symlink /file)'],
                'my_dir': ['(Dir)', {'inner': ['(File)']}],
                'my_dir_link': ['(Symlink /my_dir)'],

                'link_to_abs_link': ['(Symlink abs_link_to_file)'],
            }], render_subvol(subvol))
