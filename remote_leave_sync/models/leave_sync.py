import logging
import odoorpc
from odoo import models, fields, api
from odoo.exceptions import ValidationError, UserError

_logger = logging.getLogger(__name__)


class LeaveSyncConfig(models.Model):
    """Configuration for remote leave synchronization using OdooRPC"""
    _name = 'leave.sync.config'
    _description = "Leave Synchronization Configuration"
    _rec_name = 'config_name'

    config_name = fields.Char(string="Configuration Name", required=True)
    is_active = fields.Boolean(string="Active Sync", default=False)
    
    # Remote connection details
    sync_db_host = fields.Char(string="Remote DB URL", required=True,
                                )
    sync_db_name = fields.Char(string="Remote Database Name", required=True)
    sync_db_user = fields.Char(string="Remote DB Username", required=True)
    sync_db_password = fields.Char(string="Remote DB Password", required=True)
    
    # Sync direction options
    # sync_direction = fields.Selection([
    #     # ('local_to_remote', 'Local â†’ Remote (Push)'),
    #     # ('remote_to_local', 'Remote â†’ Local (Pull)'),
    #     # ('bidirectional', 'Bidirectional (Both Ways)')
    # ], string="Sync Direction", default='local_to_remote', required=True)
    
    # What to sync
    sync_on_request = fields.Boolean(string="Sync on Request", default=True,
                                      help="Sync when leave is requested")
    sync_on_approve = fields.Boolean(string="Sync on Approval", default=True,
                                      help="Sync when leave is approved")
    sync_on_refuse = fields.Boolean(string="Sync on Refusal", default=True,
                                     help="Sync when leave is refused")
    
    # Advanced options
    auto_approve_remote = fields.Boolean(string="Auto-Approve on Remote", default=False,
                                          help="Automatically approve leave on remote DB")
    # sync_attachments = fields.Boolean(string="Sync Attachments", default=False,
    #                                    help="Sync supporting documents/attachments")
    # timeout = fields.Integer(string="Connection Timeout (seconds)", default=120,
    #                           help="Timeout for remote operations")

    @api.constrains('is_active')
    def _check_unique_active(self):
        """Ensure only one configuration is active"""
        if self.is_active:
            if self.search_count([('is_active', '=', True), ('id', '!=', self.id)]) > 0:
                raise ValidationError("Only one configuration can be active at a time.")

    def get_odoo_connection(self):
        """
        Create and return an OdooRPC connection
        
        Returns:
            odoorpc.ODOO: Connected OdooRPC instance
        """
        self.ensure_one()
        
        try:
            # Create OdooRPC instance
            odoo = odoorpc.ODOO(
                host=self.sync_db_host,
                port=443,
                protocol='jsonrpc+ssl'
            )
            
            # Login
            odoo.login(
                db=self.sync_db_name,
                login=self.sync_db_user,
                password=self.sync_db_password
            )
            
            return odoo
            
        except odoorpc.error.RPCError as e:
            _logger.error(f"OdooRPC Error: {e}")
            raise UserError(f"Remote connection failed: {e}")
        except Exception as e:
            _logger.error(f"Connection Error: {e}")
            raise UserError(f"Unable to connect to remote database: {e}")

    def test_connection(self):
        """Test connection to remote database"""
        self.ensure_one()
        
        if not all([self.sync_db_host, self.sync_db_name, self.sync_db_user, self.sync_db_password]):
            return {
                'warning': {
                    'title': "Configuration Incomplete",
                    'message': "Please fill in all Remote DB fields before testing.",
                }
            }
        
        try:
            # Test connection
            odoo = self.get_odoo_connection()
            
            # Get current user info
            user = odoo.env.user
            user_name = user.name
            user_id = user.id
            
            # Check if hr.leave model exists
            try:
                Leave = odoo.env['hr.leave']
                leave_count = Leave.search_count([])
                
                message = (
                    f"âœ“ Connected successfully\n"
                    f"âœ“ User: {user_name} (ID: {user_id})\n"
                    f"âœ“ Database: {self.sync_db_name}\n"
                    f"âœ“ Leave module available\n"
                    f"âœ“ Found {leave_count} leave records"
                )
                
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': "Connection Successful",
                        'message': message,
                        'sticky': False,
                        'type': 'success',
                    }
                }
            except Exception as model_error:
                return {
                    'warning': {
                        'title': "Module Missing",
                        'message': f"Connection OK, but 'hr.leave' model not found. Install Time Off module on remote.\n\nError: {model_error}",
                    }
                }
        
        except UserError as e:
            return {
                'warning': {
                    'title': "Connection Failed",
                    'message': str(e),
                }
            }
        except Exception as e:
            _logger.error(f"Connection Test Error: {e}")
            return {
                'warning': {
                    'title': "Unexpected Error",
                    'message': f"Error: {str(e)}",
                }
            }


class HrEmployee(models.Model):
    """Add remote employee mapping"""
    _inherit = 'hr.employee'
    
    remote_employee_id = fields.Integer(
        string="Remote Employee ID",
        copy=False,
        help="Employee ID in the remote Odoo database"
    )
    
    @api.constrains('remote_employee_id')
    def _check_remote_employee_id_unique(self):
        """Ensure remote employee ID is unique"""
        for rec in self:
            if not rec.remote_employee_id:
                continue
            duplicate = self.search([
                ('id', '!=', rec.id),
                ('remote_employee_id', '=', rec.remote_employee_id)
            ], limit=1)
            
            if duplicate:
                raise ValidationError(
                    f"Remote Employee ID {rec.remote_employee_id} is already "
                    f"assigned to employee: {duplicate.name}"
                )


class HrLeaveType(models.Model):
    """Add remote leave type mapping"""
    _inherit = 'hr.leave.type'
    
    remote_leave_type_id = fields.Integer(
        string="Remote Leave Type ID",
        copy=False,
        help="Leave type ID in the remote Odoo database"
    )
    
    def action_fetch_remote_leave_types(self):
        """Fetch leave types from remote and help with mapping"""
        config = self.env['leave.sync.config'].search([('is_active', '=', True)], limit=1)
        if not config:
            raise UserError("No active sync configuration found.")
        
        try:
            odoo = config.get_odoo_connection()
            LeaveType = odoo.env['hr.leave.type']
            
            # Fetch all leave types from remote
            remote_type_ids = LeaveType.search([])
            
            # Read their data
            remote_types = LeaveType.read(remote_type_ids, ['name', 'allocation_validation_type'])
            
            # Format for display
            message_lines = ["Remote Leave Types:\n"]
            for rt in remote_types:
                message_lines.append(
                    f"â€¢ ID: {rt['id']} - {rt['name']} "
                    f"({rt.get('allocation_validation_type', 'N/A')})"
                )
            
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': "Remote Leave Types",
                    'message': '\n'.join(message_lines),
                    'sticky': True,
                    'type': 'info',
                }
            }
        except Exception as e:
            raise UserError(f"Failed to fetch remote leave types: {e}")


class HrLeave(models.Model):
    """Extend hr.leave to sync with remote database using OdooRPC"""
    _inherit = 'hr.leave'
    
    remote_leave_id = fields.Integer(
        string="Remote Leave ID",
        copy=False,
        help="Leave request ID in the remote Odoo database"
    )
    
    has_remote_sync = fields.Boolean(
        string="Has Remote Sync",
        compute="_compute_has_remote_sync",
        store=True,
        help="Technical field to check if leave is synced to remote"
    )
    
    sync_status = fields.Selection([
        ('not_synced', 'Not Synced'),
        ('syncing', 'Syncing...'),
        ('synced', 'Synced'),
        ('failed', 'Sync Failed'),
    ], string="Sync Status", default='not_synced', copy=False)
    
    sync_error_message = fields.Text(
        string="Sync Error", 
        copy=False,
        # groups="hr_holidays.group_hr_holidays_manager"
    )
    
    last_sync_date = fields.Datetime(
        string="Last Sync", 
        copy=False,
        # groups="hr_holidays.group_hr_holidays_manager"
    )

    remote_leave_visible = fields.Boolean(
    compute="_compute_remote_leave_visible",
    store=False
    )
    
    
    
    @api.depends()
    def _compute_remote_leave_visible(self):
        is_manager = self.env.user.has_group(
            'hr_holidays.group_hr_holidays_manager'
        )
        for rec in self:
            # use sudo() so the compute never fails
            remote_id = bool(rec.sudo().remote_leave_id)
            rec.remote_leave_visible = bool(is_manager and remote_id)

    @api.depends('remote_leave_id')
    def _compute_has_remote_sync(self):
        """Compute if leave has been synced to remote"""
        for leave in self:
            leave.has_remote_sync = bool(leave.remote_leave_id)

    @api.model_create_multi
    def create(self, vals_list):
        """Create leave and sync to remote if enabled"""
        records = super().create(vals_list)
        
        if not self.env.context.get('skip_sync'):
            config = self._get_active_config()
            if config and config.sync_on_request:
                records._sync_leave_to_remote('create')
        
        return records

    def write(self, vals):
        """Update leave and sync status changes"""
        res = super().write(vals)
        
        if self.env.context.get('skip_sync'):
            return res
        
        config = self._get_active_config()
        if not config:
            return res
        
        # Sync on state changes
        if 'state' in vals:
            new_state = vals['state']
            
            if new_state == 'validate' and config.sync_on_approve:
                self._sync_leave_to_remote('approve')
            elif new_state == 'refuse' and config.sync_on_refuse:
                self._sync_leave_to_remote('refuse')
        
        # Sync on date/duration changes
        if any(field in vals for field in ['date_from', 'date_to', 'number_of_days']):
            if self.remote_leave_id:  # Only update if already synced
                self._sync_leave_to_remote('update')
        
        return res

    def unlink(self):
        """Delete leave and sync deletion to remote"""
        if not self.env.context.get('skip_sync'):
            config = self._get_active_config()
            if config:
                self._sync_leave_to_remote('delete')
        
        return super().unlink()

    def _get_active_config(self):
        """Get active sync configuration"""
        return self.env['leave.sync.config'].sudo().search([
            ('is_active', '=', True)
        ], limit=1)

    def _sync_leave_to_remote(self, sync_type):
        """
        Main sync method - handles all sync operations using OdooRPC
        
        Args:
            sync_type: 'create', 'update', 'approve', 'refuse', 'delete'
        """
        config = self._get_active_config()
        if not config:
            _logger.info("No active leave sync config. Skipping sync.")
            return
        
        # Check sync direction
        # if config.sync_direction == 'remote_to_local':
        #     _logger.info("Sync direction is remote_to_local. Skipping local to remote sync.")
        #     return
        
        for leave in self:
            leave._sync_single_leave(config, sync_type)

    def _sync_single_leave(self, config, sync_type):
        """Sync a single leave record using OdooRPC"""
        self.ensure_one()
        
        try:
            # Update sync status
            self.with_context(skip_sync=True).write({'sync_status': 'syncing'})
            
            # Connect to remote
            odoo = config.get_odoo_connection()
            
            # Get mappings
            remote_employee_id = self.employee_id.remote_employee_id
            if not remote_employee_id:
                raise Exception(
                    f"Employee '{self.employee_id.name}' (ID: {self.employee_id.id}) "
                    f"is missing remote_employee_id. Set this in employee form."
                )
            
            remote_leave_type_id = self.holiday_status_id.remote_leave_type_id
            if not remote_leave_type_id:
                raise Exception(
                    f"Leave type '{self.holiday_status_id.name}' (ID: {self.holiday_status_id.id}) "
                    f"is missing remote_leave_type_id. Configure in Time Off Types."
                )
            
            # Access remote Leave model
            RemoteLeave = odoo.env['hr.leave']
            
            # Auto-create if missing remote_leave_id for approve/refuse/update
            if sync_type in ['approve', 'refuse', 'update'] and not self.remote_leave_id:
                _logger.warning(
                    f"Leave {self.id} has no remote_leave_id. "
                    f"Auto-creating on remote before {sync_type}."
                )
                self._remote_create_leave(odoo, RemoteLeave, remote_employee_id, remote_leave_type_id)
            
            # Perform sync operation
            if sync_type == 'create':
                self._remote_create_leave(odoo, RemoteLeave, remote_employee_id, remote_leave_type_id)
            
            elif sync_type == 'update':
                self._remote_update_leave(odoo, RemoteLeave)
            
            elif sync_type == 'approve':
                self._remote_approve_leave(odoo, RemoteLeave)
            
            elif sync_type == 'refuse':
                self._remote_refuse_leave(odoo, RemoteLeave)
            
            elif sync_type == 'delete':
                self._remote_delete_leave(odoo, RemoteLeave)
            
            # Mark as synced
            self.with_context(skip_sync=True).write({
                'sync_status': 'synced',
                'sync_error_message': False,
                'last_sync_date': fields.Datetime.now(),
            })
            
            _logger.info(
                f"[LEAVE SYNC OK] {sync_type.upper()} - Local Leave={self.id} "
                f"â†’ Remote Leave={self.remote_leave_id} | Employee={self.employee_id.name}"
            )
        
        except odoorpc.error.RPCError as e:
            error_msg = f"RPC Error: {e}"
            _logger.error(f"Leave Sync Failed for {self.employee_id.name}: {error_msg}")
            
            self.with_context(skip_sync=True).write({
                'sync_status': 'failed',
                'sync_error_message': error_msg,
            })
        
        except Exception as e:
            error_msg = str(e)
            _logger.error(f"Leave Sync Failed for {self.employee_id.name}: {error_msg}")
            
            self.with_context(skip_sync=True).write({
                'sync_status': 'failed',
                'sync_error_message': error_msg,
            })

    def _remote_create_leave(self, odoo, RemoteLeave, remote_employee_id, remote_leave_type_id):
        """
        Create leave request on remote database using OdooRPC
        
        Args:
            odoo: OdooRPC connection instance
            RemoteLeave: Remote hr.leave model
            remote_employee_id: Remote employee ID (integer)
            remote_leave_type_id: Remote leave type ID (integer)
        """
        self.ensure_one()
        
        # Prepare values
        remote_vals = {
            'employee_id': remote_employee_id,
            'holiday_status_id': remote_leave_type_id,
            'date_from': fields.Datetime.to_string(self.date_from),
            'date_to': fields.Datetime.to_string(self.date_to),
            'number_of_days': self.number_of_days,
            'name': self.name or 'Leave Request',
        }
        
        # Add optional fields
        if self.request_date_from:
            remote_vals['request_date_from'] = fields.Date.to_string(self.request_date_from)
        if self.request_date_to:
            remote_vals['request_date_to'] = fields.Date.to_string(self.request_date_to)
        
        # â­ FIX: create() returns an integer ID, not a record
        remote_leave_id = RemoteLeave.create(remote_vals)
        
        _logger.info(f"Created remote leave with ID: {remote_leave_id}")
        
        # Save remote ID locally
        self.with_context(skip_sync=True).write({'remote_leave_id': remote_leave_id})
        
        # Auto-approve if configured
        config = self._get_active_config()
        if config.auto_approve_remote and self.state == 'validate':
            # â­ FIX: Browse the ID to get a record object
            remote_leave_record = RemoteLeave.browse(remote_leave_id)
            remote_leave_record.action_approve()
            _logger.info(f"Auto-approved remote leave {remote_leave_id}")

    def _remote_update_leave(self, odoo, RemoteLeave):
        """Update leave request on remote database using OdooRPC"""
        self.ensure_one()
        
        if not self.remote_leave_id:
            _logger.warning(f"No remote_leave_id for leave {self.id}. Cannot update.")
            return
        
        # â­ FIX: Browse to get record object
        remote_leave = RemoteLeave.browse(self.remote_leave_id)
        
        # Check if it exists
        if not remote_leave.exists():
            raise Exception(f"Remote leave {self.remote_leave_id} not found")
        
        # Prepare update values
        update_vals = {
            'date_from': fields.Datetime.to_string(self.date_from),
            'date_to': fields.Datetime.to_string(self.date_to),
            'number_of_days': self.number_of_days,
            'name': self.name or 'Leave Request',
        }
        
        if self.request_date_from:
            update_vals['request_date_from'] = fields.Date.to_string(self.request_date_from)
        if self.request_date_to:
            update_vals['request_date_to'] = fields.Date.to_string(self.request_date_to)
        
        # Update using OdooRPC
        remote_leave.write(update_vals)
        _logger.info(f"Updated remote leave {self.remote_leave_id}")

    def _remote_approve_leave(self, odoo, RemoteLeave):
        """Approve leave on remote database using OdooRPC"""
        self.ensure_one()
        
        if not self.remote_leave_id:
            raise Exception(
                f"Cannot approve leave {self.id} on remote: missing remote_leave_id"
            )
        
        # â­ FIX: Browse to get record object
        remote_leave = RemoteLeave.browse(self.remote_leave_id)
        
        if not remote_leave.exists():
            raise Exception(f"Remote leave {self.remote_leave_id} not found")
        
        # Check current state
        current_state = remote_leave.state
        if current_state == 'validate':
            _logger.info(f"Remote leave {self.remote_leave_id} already approved")
            return
        
        # Call action_approve method
        remote_leave.action_approve()
        _logger.info(f"Approved remote leave {self.remote_leave_id}")

    def _remote_refuse_leave(self, odoo, RemoteLeave):
        """Refuse leave on remote database using OdooRPC"""
        self.ensure_one()
        
        if not self.remote_leave_id:
            raise Exception(
                f"Cannot refuse leave {self.id} on remote: missing remote_leave_id"
            )
        
        # â­ FIX: Browse to get record object
        remote_leave = RemoteLeave.browse(self.remote_leave_id)
        
        if not remote_leave.exists():
            raise Exception(f"Remote leave {self.remote_leave_id} not found")
        
        # Check current state
        current_state = remote_leave.state
        if current_state == 'refuse':
            _logger.info(f"Remote leave {self.remote_leave_id} already refused")
            return
        
        # Call action_refuse method
        remote_leave.action_refuse()
        _logger.info(f"Refused remote leave {self.remote_leave_id}")

    def _remote_delete_leave(self, odoo, RemoteLeave):
        """Delete leave on remote database using OdooRPC"""
        self.ensure_one()
        
        if not self.remote_leave_id:
            _logger.warning(f"No remote_leave_id for leave {self.id}. Cannot delete.")
            return
        
        # â­ FIX: Browse to get record object
        remote_leave = RemoteLeave.browse(self.remote_leave_id)
        
        if remote_leave.exists():
            remote_leave.unlink()
            _logger.info(f"Deleted remote leave {self.remote_leave_id}")
        else:
            _logger.warning(f"Remote leave {self.remote_leave_id} already deleted")

    # def action_manual_sync(self):
    #     """Manual sync button action"""
    #     for leave in self:
    #         if leave.remote_leave_id:
    #             leave._sync_leave_to_remote('update')
    #         else:
    #             leave._sync_leave_to_remote('create')
        
    #     return {
    #         'type': 'ir.actions.client',
    #         'tag': 'display_notification',
    #         'params': {
    #             'title': 'Sync Complete',
    #             'message': f'Successfully synced {len(self)} leave request(s)',
    #             'type': 'success',
    #         }
    #     }

    def action_view_remote_leave(self):
        """Open remote leave in browser"""
        self.ensure_one()
        
        if not self.remote_leave_id:
            raise ValidationError("This leave has not been synced to remote database yet.")
        
        config = self._get_active_config()
        if not config:
            raise ValidationError("No active sync configuration found.")
        
        # Construct URL to remote leave
      
        remote_url = (
            f"{config.sync_db_host}:{config}/web"
            f"#id={self.remote_leave_id}&model=hr.leave&view_type=form"
        )
        
        return {
            'type': 'ir.actions.act_url',
            'url': remote_url,
            'target': 'new',
        }

    # def action_pull_from_remote(self):
    #     """Pull leave data from remote database"""
    #     self.ensure_one()
        
    #     if not self.remote_leave_id:
    #         raise UserError("No remote leave ID to pull from.")
        
    #     config = self._get_active_config()
    #     if not config:
    #         raise UserError("No active sync configuration found.")
        
    #     try:
    #         odoo = config.get_odoo_connection()
    #         RemoteLeave = odoo.env['hr.leave']
            
    #         # â­ FIX: Browse to get record object
    #         remote_leave = RemoteLeave.browse(self.remote_leave_id)
            
    #         if not remote_leave.exists():
    #             raise UserError(f"Remote leave {self.remote_leave_id} not found")
            
    #         # Read remote data - read() returns a list of dicts
    #         remote_data_list = RemoteLeave.read(
    #             [self.remote_leave_id],
    #             ['date_from', 'date_to', 'number_of_days', 'state', 'name']
    #         )
    #         remote_data = remote_data_list[0] if remote_data_list else {}
            
    #         # Update local with remote data
    #         update_vals = {
    #             'date_from': remote_data.get('date_from'),
    #             'date_to': remote_data.get('date_to'),
    #             'number_of_days': remote_data.get('number_of_days'),
    #             'name': remote_data.get('name'),
    #         }
            
    #         # Only update state if different
    #         if remote_data.get('state') and remote_data['state'] != self.state:
    #             update_vals['state'] = remote_data['state']
            
    #         self.with_context(skip_sync=True).write(update_vals)
            
    #         return {
    #             'type': 'ir.actions.client',
    #             'tag': 'display_notification',
    #             'params': {
    #                 'title': 'Pull Successful',
    #                 'message': 'Leave data updated from remote database',
    #                 'type': 'success',
    #             }
    #         }
        
    #     except Exception as e:
    #         raise UserError(f"Failed to pull from remote: {e}")